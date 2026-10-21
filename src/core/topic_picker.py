"""Elección de temas para el modo "aleatorio".

Con IA: Gemini (o el proveedor activo) propone temas concretos del nicho evitando los ya usados.
Sin IA: se toma un tema no usado del banco curado config/topic_bank.yaml.
Los temas elegidos se guardan en output/topic_history.json para no repetirlos.
"""

from __future__ import annotations

import datetime
import json
import os
import random
import re
import threading
import unicodedata
from typing import Callable, Dict, List, Optional

import yaml

from src.core.job_manager import write_json_atomic

RANDOM_TOPIC_WORDS = {"", "aleatorio", "aleatoria", "random", "auto", "sorpresa"}
_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "y", "o", "en", "que", "por", "para", "con",
    "como", "cómo", "qué", "a", "al", "su", "sus", "se", "es", "lo", "the", "of", "and", "why", "how",
    # Verbos y muletillas de título que no distinguen un tema de otro.
    "funciona", "funcionan", "explicado", "explicada", "realmente", "pasa", "paso", "historia", "verdad",
}
_lock = threading.Lock()


def is_random_topic(topic: Optional[str]) -> bool:
    return (topic or "").strip().lower() in RANDOM_TOPIC_WORDS


def _words(text: str) -> set:
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")
    return {w for w in re.findall(r"[a-z0-9]+", plain) if w not in _STOPWORDS and len(w) > 2}


def is_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


class TopicPicker:
    def __init__(
        self,
        suggest: Optional[Callable[[str, int, List[str]], List[str]]] = None,
        bank_path: str = "config/topic_bank.yaml",
        history_path: str = "output/topic_history.json",
        avoid_window: int = 60,
    ):
        """suggest(niche, count, avoid) -> temas propuestos por IA (lista vacía si no hay IA)."""
        self.suggest = suggest
        self.bank_path = bank_path
        self.history_path = history_path
        self.avoid_window = avoid_window

    # ------------------------------------------------------------------ historial
    def _load_history(self) -> Dict[str, List[dict]]:
        try:
            with open(self.history_path, "r", encoding="utf-8") as file:
                data = json.load(file)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def used_topics(self, niche: str) -> List[str]:
        return [entry["topic"] for entry in self._load_history().get(niche, []) if entry.get("topic")]

    def used_photos(self, niche: str) -> List[str]:
        return [entry["photo"] for entry in self._load_history().get(niche, []) if entry.get("photo")]

    def remember(self, niche: str, topic: str, job_id: str = "", photo: str = "") -> None:
        with _lock:
            history = self._load_history()
            entry = {"topic": topic, "job_id": job_id, "date": datetime.date.today().isoformat()}
            if photo:
                entry["photo"] = photo
            history.setdefault(niche, []).append(entry)
            write_json_atomic(self.history_path, history)

    # ------------------------------------------------------------------ banco
    def bank(self, niche: str) -> List[str]:
        try:
            with open(self.bank_path, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
        except (OSError, yaml.YAMLError):
            return []
        topics = []
        for item in data.get(niche) or []:
            # "Tema: subtítulo" sin comillas llega de YAML como diccionario.
            text = ": ".join(f"{k}: {v}" for k, v in item.items()) if isinstance(item, dict) else str(item)
            if text.strip():
                topics.append(text.strip())
        return topics

    # ------------------------------------------------------------------ elección
    def pick(self, niche: str, job_id: str = "", remember: bool = True) -> str:
        used = self.used_topics(niche)
        recent = used[-self.avoid_window:]

        def fresh(candidates: List[str], against: List[str]) -> List[str]:
            return [c for c in candidates if not any(is_similar(c, u) for u in against)]

        topic = None
        if self.suggest:
            try:
                proposals = fresh(self.suggest(niche, 8, recent), recent)
                if proposals:
                    topic = random.choice(proposals[:4])
            except Exception as error:
                print(f"[TopicPicker] La IA no propuso temas ({error}); uso el banco curado.")

        if topic is None:
            bank = self.bank(niche)
            unused = fresh(bank, used)
            if not unused and bank:
                # Banco agotado: se reinicia empezando por los usados hace más tiempo.
                order = {t.lower(): i for i, t in enumerate(used)}
                unused = sorted(bank, key=lambda t: order.get(t.lower(), -1))[:max(1, len(bank) // 3)]
            topic = random.choice(unused) if unused else f"Un dato poco conocido sobre {niche.replace('_', ' ')}"

        if remember:
            self.remember(niche, topic, job_id)
        return topic
