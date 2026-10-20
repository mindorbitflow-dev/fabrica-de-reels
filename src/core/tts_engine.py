import os
import re
import json
import time
import asyncio
import subprocess
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

import edge_tts

TICKS_PER_SECOND = 10_000_000.0
_WORD_CHARS = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]")


@dataclass
class WordTiming:
    word: str
    start: float  # en segundos
    end: float    # en segundos
    token_index: int = -1  # posición de la palabra en spoken_tokens(texto); -1 si no se pudo alinear


@dataclass
class AudioResult:
    audio_path: str
    duration: float
    words: List[WordTiming]

    def save_words(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as file:
            json.dump([asdict(w) for w in self.words], file, ensure_ascii=False)

    @staticmethod
    def load(audio_path: str, words_path: str) -> "AudioResult":
        with open(words_path, "r", encoding="utf-8") as file:
            words = [WordTiming(**w) for w in json.load(file)]
        return AudioResult(audio_path, get_media_duration(audio_path), words)


def get_media_duration(path: str) -> float:
    """Duración exacta en segundos con ffprobe. Lanza RuntimeError si no se puede leer."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(json.loads(res.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"No se pudo leer la duración de {path}: {res.stderr.strip()[:200]}") from error


def spoken_tokens(text: str) -> List[str]:
    """Tokens hablables del texto (separados por espacios y con al menos una letra o número)."""
    return [t for t in (text or "").split() if _WORD_CHARS.search(t)]


class TTSEngine:
    def __init__(self, default_voice: str = "es-MX-JorgeNeural", rate: str = "+5%", pitch: str = "+0Hz"):
        self.default_voice = default_voice
        self.rate = rate
        self.pitch = pitch

    def synthesize(self, text: str, output_path: str, voice: Optional[str] = None, rate: Optional[str] = None, retries: int = 3) -> AudioResult:
        """
        Genera el archivo de audio y extrae los timestamps reales de cada palabra.
        Método sincrónico que envuelve el flujo asincrónico de Edge-TTS con reintentos.
        """
        voice_to_use = voice or self.default_voice
        rate_to_use = rate or self.rate
        if not (text or "").strip():
            raise ValueError("El guion no tiene texto para locutar.")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                return asyncio.run(self._synthesize_edge_async(text, output_path, voice_to_use, rate_to_use))
            except Exception as error:
                last_error = error
                print(f"[TTSEngine] Intento {attempt + 1}/{retries} fallido: {error}")
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Edge-TTS no pudo sintetizar la voz tras {retries} intentos: {last_error}")

    async def _synthesize_edge_async(self, text: str, output_path: str, voice: str, rate: str) -> AudioResult:
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=self.pitch, boundary="WordBoundary")

        boundaries = []
        tmp_path = output_path + ".part"
        with open(tmp_path, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    boundaries.append(chunk)
        if os.path.getsize(tmp_path) < 1024:
            raise RuntimeError("Edge-TTS devolvió un audio vacío.")
        os.replace(tmp_path, output_path)

        total_duration = get_media_duration(output_path)
        words = self._calculate_word_timings(text, boundaries, total_duration)
        return AudioResult(audio_path=output_path, duration=total_duration, words=words)

    def _calculate_word_timings(self, full_text: str, boundaries: List[Dict[str, Any]], total_duration: float) -> List[WordTiming]:
        """Convierte los límites de Edge-TTS en tiempos por palabra con la puntuación original."""
        word_marks = [b for b in boundaries if b.get("type") == "WordBoundary" and b.get("text", "").strip()]
        if word_marks:
            raw = [
                WordTiming(
                    word=b["text"].strip(),
                    start=b.get("offset", 0) / TICKS_PER_SECOND,
                    end=(b.get("offset", 0) + b.get("duration", 0)) / TICKS_PER_SECOND,
                )
                for b in word_marks
            ]
            return self._attach_punctuation(full_text, raw)

        # Sin marcas por palabra: repartir cada frase según la longitud de sus palabras.
        all_words: List[WordTiming] = []
        sentence_marks = [b for b in boundaries if b.get("text", "").strip()]
        segments = [
            (b["text"].strip(), b.get("offset", 0) / TICKS_PER_SECOND, b.get("duration", 0) / TICKS_PER_SECOND)
            for b in sentence_marks
        ] or [(full_text, 0.0, total_duration)]
        for s_text, s_start, s_dur in segments:
            s_words = s_text.split()
            if not s_words:
                continue
            weights = [max(1, len(re.sub(r"[^\w]", "", w))) for w in s_words]
            total_weight = sum(weights)
            curr_time = s_start
            for w, weight in zip(s_words, weights):
                word_dur = (weight / total_weight) * s_dur
                all_words.append(WordTiming(word=w, start=curr_time, end=curr_time + word_dur))
                curr_time += word_dur
        return all_words

    @staticmethod
    def _attach_punctuation(full_text: str, raw_words: List[WordTiming]) -> List[WordTiming]:
        """Edge-TTS entrega palabras sin puntuación; se recupera del texto para cortar
        los subtítulos en las pausas naturales. Si el texto no se puede alinear, se
        conservan las palabras tal cual."""
        tokens = spoken_tokens(full_text)
        cursor = 0
        result = []
        for timing in raw_words:
            spoken = re.sub(r"\W", "", timing.word.lower())
            match = None
            for offset in range(cursor, min(cursor + 4, len(tokens))):
                if re.sub(r"\W", "", tokens[offset].lower()).startswith(spoken[:3]):
                    match = offset
                    break
            if match is None:
                result.append(timing)
                continue
            token = tokens[match]
            # Conservar signos de apertura/cierre del token original (¿, ¡, comas, puntos...).
            display = token if re.sub(r"\W", "", token.lower()) == spoken else timing.word
            result.append(WordTiming(word=display, start=timing.start, end=timing.end, token_index=match))
            cursor = match + 1
        return result

    @staticmethod
    def list_spanish_voices() -> List[Dict[str, str]]:
        """Lista las mejores voces neuronales en español recomendadas."""
        return [
            {"id": "es-MX-JorgeNeural", "name": "Jorge (México - Narración Dinámica / Podcast)", "gender": "Hombre"},
            {"id": "es-MX-DaliaNeural", "name": "Dalia (México - Expresiva y Natural)", "gender": "Mujer"},
            {"id": "es-ES-AlvaroNeural", "name": "Álvaro (España - Enérgico / Viral)", "gender": "Hombre"},
            {"id": "es-ES-ElviraNeural", "name": "Elvira (España - Clara / Documental)", "gender": "Mujer"},
            {"id": "es-CO-GonzaloNeural", "name": "Gonzalo (Colombia - Casual / Cercano)", "gender": "Hombre"},
            {"id": "es-CO-SalomeNeural", "name": "Salomé (Colombia - Suave / Educativo)", "gender": "Mujer"},
            {"id": "es-AR-TomasNeural", "name": "Tomás (Argentina - Firme)", "gender": "Hombre"},
            {"id": "en-US-ChristopherNeural", "name": "Christopher (EEUU - Inglés Profundo)", "gender": "Hombre"},
            {"id": "en-US-JennyNeural", "name": "Jenny (EEUU - Inglés Enérgico)", "gender": "Mujer"}
        ]
