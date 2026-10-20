"""Router de proveedores LLM compartido por el guionista y el copiloto de edición.

Recorre los proveedores configurados en orden. Un error de autenticación
(401/403) descarta el proveedor completo de inmediato en lugar de probar
modelo tras modelo con la misma clave inválida.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

from src.core.env import load_env

PROVIDER_ORDER = ("custom", "gemini", "openai", "groq")

# Modelos gratuitos de OpenRouter comprobados en su catálogo (2026-09-23), en orden de
# preferencia para guiones en español. Si uno está saturado (429) se pasa al siguiente.
# Se pueden sustituir con CUSTOM_LLM_FALLBACK_MODELS en el .env (separados por coma).
DEFAULT_CUSTOM_FALLBACK_MODELS = (
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "z-ai/glm-5.2:free",
)


# Modelos Gemini probados con una clave gratuita nueva (2026-09-23): gemini-2.5-flash ya no
# está disponible para usuarios nuevos (404) y los demás alternan picos de demanda (503).
# Se prueban en orden; GEMINI_MODEL fija el primero y GEMINI_FALLBACK_MODELS la lista.
DEFAULT_GEMINI_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
# Esperas entre rondas completas cuando todos los modelos están saturados (503). El guion es lo
# que más pesa en el resultado: vale más esperar un par de minutos que publicar uno genérico.
GEMINI_RETRY_WAITS = (0, 10, 30, 60)
# Los modelos Gemini que razonan descuentan el pensamiento de max_output_tokens (medido el
# 2026-10-19: 2399 tokens pensando dejaron 96 para el guion y el JSON salió cortado). Este
# margen se suma para que max_tokens siga siendo el espacio de la respuesta.
GEMINI_THINKING_ROOM = 8192


# Groq retira y renombra modelos con frecuencia (un nombre fijo acaba en 404). Se consulta su
# catálogo con la clave del usuario y se ordena por aptitud para escribir guiones.
_GROQ_SKIP = ("whisper", "guard", "tts", "embed", "orpheus", "playai", "distil", "compound", "vision", "safeguard")
_GROQ_PREFER = ("gpt-oss-120b", "llama-4-maverick", "kimi", "70b", "qwen", "llama-4-scout", "gpt-oss-20b", "32b")
_groq_cache: dict = {}


def discover_groq_models(api_key: str) -> List[str]:
    """Modelos de chat activos en Groq, del más apto al menos apto. Lista vacía si no se puede consultar."""
    if not api_key:
        return []
    if api_key in _groq_cache:
        return _groq_cache[api_key]
    import requests
    try:
        response = requests.get("https://api.groq.com/openai/v1/models",
                                headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
        data = response.json().get("data", []) if response.ok else []
    except Exception:
        return []
    ids = [m["id"] for m in data if m.get("active", True) and not any(x in m["id"].lower() for x in _GROQ_SKIP)]

    def rank(model_id: str):
        lowered = model_id.lower()
        return next((i for i, marker in enumerate(_GROQ_PREFER) if marker in lowered), len(_GROQ_PREFER))

    ordered = sorted(ids, key=rank)[:5]
    _groq_cache[api_key] = ordered
    return ordered


class LLMUnavailableError(RuntimeError):
    """Ningún proveedor configurado pudo responder."""


def is_auth_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if status in (401, 403):
        return True
    text = str(error).lower()
    return any(marker in text for marker in ("error code: 401", "error code: 403", "invalid api key", "user not found",
                                             "permission denied", "api key not valid", "unauthorized"))


# Cupo de la CUENTA (no de un modelo). En Gemini los límites gratuitos son por modelo y se
# gestionan dentro de _gemini_call pasando al siguiente modelo.
QUOTA_MARKERS = ("free-models-per-day", "add 10 credits", "insufficient credits", "insufficient_quota")


def is_quota_error(error: Exception) -> bool:
    """Cupo de la cuenta agotado (p. ej. 50 peticiones/día gratis en OpenRouter). No depende del modelo:
    probar otros modelos del mismo proveedor solo gasta más peticiones."""
    text = str(error).lower()
    return any(marker in text for marker in QUOTA_MARKERS)


def describe_error(provider: str, error: Exception) -> str:
    """Motivo legible para mostrar en la interfaz."""
    names = {"custom": "OpenRouter/IA personalizada", "gemini": "Gemini", "openai": "OpenAI", "groq": "Groq"}
    name = names.get(provider, provider)
    if is_auth_error(error):
        return f"{name}: la clave no es válida."
    if is_quota_error(error):
        if "add 10 credits" in str(error).lower():
            return (f"{name}: se agotó el límite diario gratuito (50 peticiones/día). Se reinicia a las 00:00 UTC; "
                    "con una recarga única de 10 créditos sube a 1000 peticiones/día en modelos gratuitos.")
        return f"{name}: se agotó el cupo de la cuenta."
    return f"{name}: {_safe(error)[:160]}"


class LLMRouter:
    def __init__(self):
        load_env()
        # Proveedores descartados por credenciales inválidas, indexados por (proveedor, clave):
        # si el usuario corrige la clave desde la UI, el proveedor vuelve a estar disponible.
        self.disabled: set = set()
        # Último motivo por el que falló cada proveedor, para explicarlo en la interfaz.
        self.failures: dict = {}

    @property
    def keys(self) -> dict:
        return {
            "custom": os.getenv("CUSTOM_LLM_API_KEY", "").strip(),
            "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
            "openai": os.getenv("OPENAI_API_KEY", "").strip(),
            "groq": os.getenv("GROQ_API_KEY", "").strip(),
        }

    @property
    def custom_base_url(self) -> str:
        return (os.getenv("CUSTOM_LLM_BASE_URL") or "https://openrouter.ai/api/v1").strip().rstrip("/")

    @property
    def custom_models(self) -> List[str]:
        primary = (os.getenv("CUSTOM_LLM_MODEL") or DEFAULT_CUSTOM_FALLBACK_MODELS[0]).strip()
        fallback_env = os.getenv("CUSTOM_LLM_FALLBACK_MODELS")
        fallbacks = [m.strip() for m in fallback_env.split(",")] if fallback_env is not None else list(DEFAULT_CUSTOM_FALLBACK_MODELS)
        return list(dict.fromkeys(m for m in [primary, *fallbacks] if m))

    @property
    def gemini_models(self) -> List[str]:
        primary = (os.getenv("GEMINI_MODEL") or "").strip()
        fallback_env = os.getenv("GEMINI_FALLBACK_MODELS")
        fallbacks = [m.strip() for m in fallback_env.split(",")] if fallback_env is not None else list(DEFAULT_GEMINI_MODELS)
        return list(dict.fromkeys(m for m in [primary, *fallbacks] if m))

    def _gemini_call(self, build_config, contents: str):
        """Prueba los modelos Gemini en orden; un modelo saturado (503) o retirado (404) pasa al siguiente."""
        from google import genai
        client = genai.Client(api_key=self.keys["gemini"])
        last_error: Optional[Exception] = None
        # Los picos de demanda (503) suelen durar segundos: si todos los modelos están
        # saturados se espera y se reintenta la ronda completa.
        for round_index, wait in enumerate(GEMINI_RETRY_WAITS):
            if wait:
                print(f"[LLM] Gemini saturado; reintento en {wait}s...")
                time.sleep(wait)
            for model in self.gemini_models:
                try:
                    response = client.models.generate_content(model=model, contents=contents, config=build_config())
                    finish = response.candidates[0].finish_reason if response.candidates else None
                    if str(finish).endswith("MAX_TOKENS"):
                        raise RuntimeError(f"respuesta cortada por límite de tokens ({model})")
                    return response
                except Exception as error:
                    if is_auth_error(error):
                        raise
                    # 404 (modelo retirado), 429 (cupo de ese modelo) o 503 (saturado): siguiente modelo.
                    last_error = error
                    if round_index == 0:
                        print(f"[LLM] gemini/{model} no disponible: {_safe(error)[:100]}")
            if "503" not in str(last_error) and "unavailable" not in str(last_error).lower():
                break
        raise last_error or RuntimeError("Sin modelos Gemini disponibles")


    @property
    def models(self) -> dict:
        return {
            "gemini": os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODELS[0],
            "openai": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "groq": os.getenv("GROQ_MODEL", ""),
        }

    def available_providers(self, preferred: str = "auto") -> List[str]:
        keys = self.keys
        providers = [p for p in PROVIDER_ORDER if keys.get(p) and (p, keys[p]) not in self.disabled]
        if preferred in providers:
            providers.remove(preferred)
            providers.insert(0, preferred)
        return providers

    @property
    def has_any(self) -> bool:
        return bool(self.available_providers())

    def complete(
        self,
        prompt: str,
        provider: str,
        json_mode: bool = True,
        max_tokens: int = 4096,
        temperature: float = 0.75,
        timeout: float = 90.0,
    ) -> str:
        """Llama a un proveedor concreto. Lanza la excepción original si falla."""
        if provider == "gemini":
            def build_config():
                config = {"temperature": temperature, "max_output_tokens": max_tokens + GEMINI_THINKING_ROOM}
                if json_mode:
                    config["response_mime_type"] = "application/json"
                return config
            return self._gemini_call(build_config, prompt).text or ""

        from openai import OpenAI
        if provider == "custom":
            client = OpenAI(
                base_url=self.custom_base_url,
                api_key=self.keys["custom"],
                default_headers={"HTTP-Referer": "http://localhost:8501", "X-Title": "AI Reel Factory"},
                timeout=timeout,
                max_retries=1,
            )
            models = self.custom_models
        elif provider == "groq":
            client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=self.keys["groq"], timeout=timeout, max_retries=1)
            configured = os.getenv("GROQ_MODEL", "").strip()
            models = list(dict.fromkeys(m for m in [configured, *discover_groq_models(self.keys["groq"])] if m))
            if not models:
                raise RuntimeError("Groq no devolvió modelos disponibles (¿clave válida?).")
        else:
            client = OpenAI(api_key=self.keys["openai"], timeout=timeout, max_retries=1)
            models = [self.models["openai"]]

        last_error: Optional[Exception] = None
        for model in models:
            try:
                kwargs = {}
                if json_mode and provider != "custom":
                    kwargs["response_format"] = {"type": "json_object"}
                suffix = "\n\nIMPORTANTE: Responde ÚNICAMENTE con el objeto JSON válido." if json_mode else ""
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt + suffix}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
                return response.choices[0].message.content or ""
            except Exception as error:
                if is_auth_error(error) or is_quota_error(error):
                    raise
                last_error = error
                print(f"[LLM] {provider}/{model} no disponible: {_safe(error)}")
        raise last_error or RuntimeError(f"Sin modelos para {provider}")

    def ping(self, provider: str) -> Tuple[bool, str]:
        """Llamada real mínima para saber si la clave funciona (no solo si existe)."""
        if not self.keys.get(provider):
            return False, "sin clave"
        try:
            self.complete("Responde solo: OK", provider, json_mode=False, max_tokens=5, temperature=0, timeout=30)
            return True, "OK"
        except Exception as error:
            self.handle_error(provider, error)
            return False, self.failures.get(provider, _safe(error)[:120])

    def complete_grounded(self, prompt: str, max_tokens: int = 4096, timeout: float = 120.0) -> Tuple[str, List[dict]]:
        """Gemini con búsqueda de Google. Devuelve (texto, fuentes [{title, url}]).
        Lanza excepción si no hay clave de Gemini o se agotó la cuota de búsqueda."""
        if not self.keys.get("gemini"):
            raise LLMUnavailableError("La búsqueda web requiere GEMINI_API_KEY.")
        from google.genai import types
        response = self._gemini_call(lambda: types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=max_tokens + GEMINI_THINKING_ROOM,
            temperature=0.2,
        ), prompt)
        sources = []
        for candidate in response.candidates or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            for chunk in (getattr(metadata, "grounding_chunks", None) or []):
                web = getattr(chunk, "web", None)
                if web and getattr(web, "uri", None):
                    sources.append({"title": getattr(web, "title", "") or "", "url": web.uri})
        return response.text or "", sources

    def complete_any(self, prompt: str, preferred: str = "auto", **kwargs) -> Tuple[str, str]:
        """Prueba cada proveedor disponible y devuelve (texto, proveedor)."""
        errors = []
        for provider in self.available_providers(preferred):
            try:
                return self.complete(prompt, provider, **kwargs), provider
            except Exception as error:
                self.handle_error(provider, error)
                errors.append(f"{provider}: {_safe(error)}")
        raise LLMUnavailableError("; ".join(errors) or "No hay proveedores LLM configurados.")

    def handle_error(self, provider: str, error: Exception) -> None:
        self.failures[provider] = describe_error(provider, error)
        if is_auth_error(error) or is_quota_error(error):
            self.disabled.add((provider, self.keys.get(provider)))
        print(f"[LLM] {self.failures[provider]}")

    def failure_summary(self) -> str:
        if not any(self.keys.values()):
            return "No hay ninguna clave de IA configurada (Ajustes › API Keys)."
        return " ".join(self.failures.values()) or "El proveedor de IA no respondió."


def _safe(error: Exception) -> str:
    return str(error).encode("ascii", "replace").decode("ascii")[:300]
