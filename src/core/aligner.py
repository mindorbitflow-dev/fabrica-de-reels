"""Alineación forzada voz ↔ guion con Whisper local (faster-whisper, CPU, int8).

Sirve para voces que no entregan tiempos por palabra (Gemini TTS) y como control de
calidad: detecta si la voz leyó texto que no está en el guion (p. ej. una instrucción)
o si se saltó partes. Medido en este equipo: modelo "base" carga en ~10 s y alinea
12 s de audio en ~2 s.
"""

from __future__ import annotations

import difflib
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

from src.core.tts_engine import WordTiming, spoken_tokens

_model_lock = threading.Lock()
_models = {}


def _norm(token: str) -> str:
    plain = unicodedata.normalize("NFKD", token.lower()).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", plain)


@dataclass
class Alignment:
    words: List[WordTiming]      # una entrada por token hablable del guion, con su puntuación
    coverage: float              # fracción de tokens del guion reconocidos en el audio
    extra_leading_seconds: float # audio al inicio que no pertenece al guion (a recortar)
    transcript: str


def _get_model(size: str):
    with _model_lock:
        if size not in _models:
            from faster_whisper import WhisperModel
            _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
        return _models[size]


def align_words(script_tokens: List[str], heard: List[WordTiming]) -> Alignment:
    """Empareja los tokens del guion con las palabras oídas. Los tokens no reconocidos
    reciben tiempos interpolados entre sus vecinos reconocidos."""
    script_norm = [_norm(t) for t in script_tokens]
    heard_norm = [_norm(w.word) for w in heard]
    matcher = difflib.SequenceMatcher(a=script_norm, b=heard_norm, autojunk=False)
    matched = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            matched[block.a + k] = block.b + k
    coverage = len(matched) / max(1, len(script_tokens))

    times: List[Optional[tuple]] = [None] * len(script_tokens)
    for i, j in matched.items():
        times[i] = (heard[j].start, heard[j].end)
    known = [i for i, t in enumerate(times) if t]
    if not known:
        return Alignment([], 0.0, 0.0, " ".join(w.word for w in heard))

    for i in range(len(times)):
        if times[i]:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is not None and nxt is not None:
            span_start, span_end = times[prev][1], times[nxt][0]
            gap_count = nxt - prev - 1
            step = max(0.05, (span_end - span_start) / max(1, gap_count))
            offset = i - prev - 1
            times[i] = (span_start + offset * step, span_start + (offset + 1) * step)
        elif prev is not None:
            base = times[prev][1] + 0.3 * (i - prev - 1)
            times[i] = (base, base + 0.3)
        else:
            base = max(0.0, times[nxt][0] - 0.3 * (nxt - i))
            times[i] = (base, base + 0.3)

    words = [WordTiming(token, start, max(end, start + 0.05), index)
             for index, (token, (start, end)) in enumerate(zip(script_tokens, times))]
    first_script_heard = min(matched.values())
    leading = heard[first_script_heard].start if first_script_heard > 0 else 0.0
    return Alignment(words, coverage, leading, " ".join(w.word for w in heard))


def transcribe_words(audio_path: str, language: str = "es", model_size: str = "base") -> List[WordTiming]:
    model = _get_model(model_size)
    segments, _ = model.transcribe(audio_path, language=language, word_timestamps=True, vad_filter=False)
    return [WordTiming(w.word.strip(), float(w.start), float(w.end)) for s in segments for w in (s.words or []) if w.word.strip()]


def align_audio(audio_path: str, script_text: str, language: str = "es", model_size: str = "base") -> Alignment:
    return align_words(spoken_tokens(script_text), transcribe_words(audio_path, language, model_size))
