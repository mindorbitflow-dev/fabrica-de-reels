"""Voz neuronal de Gemini (TTS) con la misma clave gratuita del guionista.

Probado el 2026-09-23 con una clave gratuita: gemini-3.8-flash-tts genera ~12 s de audio en ~6 s.
Este modelo no admite instrucciones de sistema y lee en voz alta cualquier directiva de estilo
incluida en el texto, por eso el tono se controla eligiendo la voz y el texto va limpio.
El resultado siempre pasa por el alineador (Whisper) antes de usarse.
"""

from __future__ import annotations

import os
import re
import time
import wave
from typing import List, Optional

from src.core.env import load_env

SAMPLE_RATE = 24000
TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview")
MAX_CHUNK_CHARS = 700
DEFAULT_VOICE = "Charon"


def split_for_tts(text: str, limit: int = MAX_CHUNK_CHARS) -> List[str]:
    """Trocea en frases completas sin pasar del límite de caracteres por petición."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


class GeminiVoice:
    def __init__(self, models=TTS_MODELS):
        load_env()
        self.models = list(models)

    @property
    def available(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY"))

    def _synthesize_chunk(self, client, text: str, voice: str) -> bytes:
        from google.genai import types
        from src.core.llm_client import is_auth_error
        last_error: Optional[Exception] = None
        for wait in (0, 8, 20):
            if wait:
                time.sleep(wait)
            for model in self.models:
                try:
                    response = client.models.generate_content(
                        model=model, contents=text,
                        config=types.GenerateContentConfig(
                            response_modalities=["AUDIO"],
                            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
                        ),
                    )
                    data = response.candidates[0].content.parts[0].inline_data.data
                    if data and len(data) > SAMPLE_RATE:  # al menos ~0.5 s
                        return data
                    raise RuntimeError("audio vacío")
                except Exception as error:
                    if is_auth_error(error):
                        raise
                    last_error = error
            if "503" not in str(last_error) and "unavailable" not in str(last_error).lower():
                break
        raise last_error or RuntimeError("Gemini TTS no respondió")

    def synthesize(self, text: str, output_path: str, voice: str = DEFAULT_VOICE) -> str:
        """Genera un WAV mono 24 kHz. Los trozos se unen con 0.15 s de silencio."""
        from google import genai
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        pcm = b""
        silence = b"\x00\x00" * int(SAMPLE_RATE * 0.15)
        for index, chunk in enumerate(split_for_tts(text)):
            pcm += (silence if index else b"") + self._synthesize_chunk(client, chunk, voice)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        tmp = output_path + ".part"
        with wave.open(tmp, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(SAMPLE_RATE)
            file.writeframes(pcm)
        os.replace(tmp, output_path)
        return output_path
