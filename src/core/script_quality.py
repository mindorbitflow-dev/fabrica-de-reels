"""Reglas editoriales y de duración para los guiones narrados.

Este módulo no intenta escribir el guion. Define el contrato que debe cumplir:
un texto hablado tiene que caber en el tiempo pedido y cada escena debe aportar
una idea distinta al relato.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional


# Calibrado con las locuciones reales de Edge-TTS en español a +5%:
# los reels generados midieron entre 2.0 y 2.4 palabras/seg (media ~2.2).
WORDS_PER_SECOND = 2.2
# Inglés medido con en-US-Christopher/Jenny a +5%: 2.69-2.74 palabras/seg.
WORDS_PER_SECOND_BY_LANGUAGE = {"es": WORDS_PER_SECOND, "en": 2.7}
# Voz de Gemini medida en un guion real de 152 palabras: 56.2 s -> 2.7 palabras/seg en español
# (más rápida que Edge). Sin esta corrección un TikTok de 70 s salía de 56 s y no era monetizable.
WORDS_PER_SECOND_BY_ENGINE = {("gemini", "es"): 2.7, ("gemini", "en"): 2.9}


def speech_rate(language: str = "es", engine: str = "edge") -> float:
    return WORDS_PER_SECOND_BY_ENGINE.get((engine, language), WORDS_PER_SECOND_BY_LANGUAGE.get(language, WORDS_PER_SECOND))
DURATION_TOLERANCE = 0.12
# Tolerancia amplia para aceptar un guion de LLM tras agotar reintentos:
# la duración final se mide sobre el audio real, así que un desvío moderado
# es preferible a caer en el guion genérico offline.
RELAXED_DURATION_TOLERANCE = 0.30
MAX_SCENES = 30
FORCED_EDITORIAL_PATTERNS = (
    "nos ocultaron",
    "la evidencia que acaba de salir a la luz",
    "la verdad revelada",
    "la conclusión indiscutible",
    "realidad mucho más vasta",
    "durante años",
    "nos ocultaron esto",
)


@dataclass(frozen=True)
class ScriptPlan:
    target_duration: int
    scene_count: int
    target_words: int
    min_words: int
    max_words: int
    scene_word_budgets: tuple[int, ...]


@dataclass(frozen=True)
class ScriptAssessment:
    word_count: int
    estimated_duration: float
    duration_ok: bool
    scene_count_ok: bool
    duplicate_scene_count: int
    forced_editorial_pattern_count: int
    issues: tuple[str, ...]

    @property
    def strict_ok(self) -> bool:
        return (
            self.duration_ok
            and self.scene_count_ok
            and not self.duplicate_scene_count
            and not self.forced_editorial_pattern_count
        )


def count_words(text: str) -> int:
    """Cuenta palabras hablables, incluidas palabras españolas con acentos."""
    return len(re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+(?:['’-][\wáéíóúüñÁÉÍÓÚÜÑ]+)?", text or ""))


def _default_scene_count(duration: int) -> int:
    """Un plano narrativo cada ~5-6s en formato corto, ~20s en formato largo."""
    if duration <= 20:
        return 3
    if duration <= 45:
        return 5
    if duration <= 75:
        return 8
    if duration <= 150:
        return 12
    if duration <= 360:
        return 20
    return MAX_SCENES


def create_script_plan(target_duration: int, scene_count: Optional[int] = None, language: str = "es",
                       engine: str = "edge") -> ScriptPlan:
    """Crea un presupuesto de palabras y escenas para una narración natural."""
    duration = max(10, int(target_duration or 30))
    scenes = min(MAX_SCENES, max(1, int(scene_count))) if scene_count else _default_scene_count(duration)
    wps = speech_rate(language, engine)
    target_words = max(20, round(duration * wps))
    min_words = math.floor(target_words * (1 - DURATION_TOLERANCE))
    max_words = math.ceil(target_words * (1 + DURATION_TOLERANCE))

    base, remainder = divmod(target_words, scenes)
    budgets = tuple(base + (1 if i < remainder else 0) for i in range(scenes))
    return ScriptPlan(duration, scenes, target_words, min_words, max_words, budgets)


def estimate_duration(text: str) -> float:
    return count_words(text) / WORDS_PER_SECOND


def _normalize_scene(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").lower())


def assess_script(
    full_text: str,
    scene_texts: Iterable[str],
    plan: ScriptPlan,
    tolerance: float = DURATION_TOLERANCE,
) -> ScriptAssessment:
    """Evalúa hechos objetivos: duración, número de escenas y repetición literal."""
    scenes = list(scene_texts)
    normalized = [_normalize_scene(scene) for scene in scenes if _normalize_scene(scene)]
    duplicate_count = len(normalized) - len(set(normalized))
    lowered_text = (full_text or "").lower()
    forced_pattern_count = sum(pattern in lowered_text for pattern in FORCED_EDITORIAL_PATTERNS)
    words = count_words(full_text)
    min_words = math.floor(plan.target_words * (1 - tolerance))
    max_words = math.ceil(plan.target_words * (1 + tolerance))
    duration_ok = min_words <= words <= max_words
    issues = []
    if not duration_ok:
        issues.append(
            f"El guion tiene {words} palabras; el presupuesto para {plan.target_duration}s es "
            f"{min_words}-{max_words}."
        )
    if len(scenes) != plan.scene_count:
        issues.append(f"El guion tiene {len(scenes)} escenas; se esperaban {plan.scene_count}.")
    if duplicate_count:
        issues.append(f"El guion repite {duplicate_count} escena(s).")
    if forced_pattern_count:
        issues.append("El guion usa fórmulas editoriales forzadas o sensacionalistas.")
    return ScriptAssessment(
        word_count=words,
        estimated_duration=words / WORDS_PER_SECOND,
        duration_ok=duration_ok,
        scene_count_ok=len(scenes) == plan.scene_count,
        duplicate_scene_count=duplicate_count,
        forced_editorial_pattern_count=forced_pattern_count,
        issues=tuple(issues),
    )
