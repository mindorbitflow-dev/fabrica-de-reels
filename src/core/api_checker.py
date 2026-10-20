"""
API Diagnostic and Health Checker Module
Permite probar y validar la conectividad y validez de cada API de forma individual o conjunta.
"""

import os
import time
import requests
from typing import Dict, Any

class APIChecker:
    @staticmethod
    def test_gemini(api_key: str) -> Dict[str, Any]:
        """Prueba la API de Google Gemini."""
        if not api_key:
            return {"service": "Google Gemini", "status": False, "message": "Clave no configurada.", "latency": 0}
        
        t0 = time.time()
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL") or "gemini-flash-lite-latest",
                contents="Responde únicamente: 'OK Gemini funcionando'"
            )
            elapsed = time.time() - t0
            return {
                "service": "Google Gemini",
                "status": True,
                "message": f"Conexión exitosa. Respuesta: '{response.text.strip()}'",
                "latency": round(elapsed, 2)
            }
        except Exception as e:
            elapsed = time.time() - t0
            return {"service": "Google Gemini", "status": False, "message": f"Error: {str(e)}", "latency": round(elapsed, 2)}

    @staticmethod
    def test_openai(api_key: str) -> Dict[str, Any]:
        """Prueba la API de OpenAI."""
        if not api_key:
            return {"service": "OpenAI", "status": False, "message": "Clave no configurada.", "latency": 0}
        
        t0 = time.time()
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Responde 'OK OpenAI'"}],
                max_tokens=10
            )
            elapsed = time.time() - t0
            return {
                "service": "OpenAI",
                "status": True,
                "message": f"Conexión exitosa. Respuesta: '{response.choices[0].message.content.strip()}'",
                "latency": round(elapsed, 2)
            }
        except Exception as e:
            elapsed = time.time() - t0
            return {"service": "OpenAI", "status": False, "message": f"Error: {str(e)}", "latency": round(elapsed, 2)}

    @staticmethod
    def test_groq(api_key: str) -> Dict[str, Any]:
        """Prueba la API de Groq Cloud."""
        if not api_key:
            return {"service": "Groq Cloud", "status": False, "message": "Clave no configurada.", "latency": 0}
        
        t0 = time.time()
        try:
            from openai import OpenAI
            from src.core.llm_client import discover_groq_models
            models = discover_groq_models(api_key)
            if not models:
                return {"service": "Groq Cloud", "status": False, "latency": round(time.time() - t0, 2),
                        "message": "Clave inválida o sin modelos disponibles (revisa la clave en console.groq.com)."}
            client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
            response = client.chat.completions.create(
                model=models[0],
                messages=[{"role": "user", "content": "Responde 'OK Groq'"}],
                max_tokens=10
            )
            elapsed = time.time() - t0
            return {
                "service": "Groq Cloud",
                "status": True,
                "message": f"Conexión exitosa con {models[0]}. Respuesta: '{(response.choices[0].message.content or '').strip()}'",
                "latency": round(elapsed, 2)
            }
        except Exception as e:
            elapsed = time.time() - t0
            return {"service": "Groq Cloud", "status": False, "message": f"Error: {str(e)}", "latency": round(elapsed, 2)}

    @staticmethod
    def test_custom_llm(base_url: str, api_key: str, model: str) -> Dict[str, Any]:
        """Prueba cualquier IA compatible con OpenAI (OpenRouter, DeepSeek, Ollama, etc.)."""
        clean_url = (base_url or "").strip().rstrip("/")
        if not clean_url:
            clean_url = "https://openrouter.ai/api/v1"
            
        clean_key = (api_key or "").strip()
        clean_model = (model or "").strip() or "meta-llama/llama-3.3-70b-instruct:free"

        if not clean_key and not ("localhost" in clean_url or "127.0.0.1" in clean_url):
            return {"service": "IA Personalizada", "status": False, "message": "Clave de API no configurada.", "latency": 0}
        
        t0 = time.time()
        try:
            from openai import OpenAI
            headers = {
                "HTTP-Referer": "http://localhost:8501",
                "X-Title": "AI Reel Factory"
            }
            client = OpenAI(
                base_url=clean_url,
                api_key=clean_key or "sk-dummy",
                default_headers=headers
            )
            response = client.chat.completions.create(
                model=clean_model,
                messages=[{"role": "user", "content": "Responde únicamente: 'OK OpenRouter Funcionando'"}],
                max_tokens=25
            )
            elapsed = time.time() - t0
            reply = response.choices[0].message.content.strip()
            return {
                "service": f"IA Personalizada ({clean_model})",
                "status": True,
                "message": f"Conexión exitosa con {clean_url}. Respuesta: '{reply}'",
                "latency": round(elapsed, 2)
            }
        except Exception as e:
            elapsed = time.time() - t0
            err_msg = str(e)
            
            # Mensajes de ayuda contextualizados
            help_tip = ""
            if "model_not_found" in err_msg.lower() or "not found" in err_msg.lower() or "404" in err_msg:
                help_tip = " -> En OpenRouter los modelos llevan prefijo (ej: 'meta-llama/llama-3.3-70b-instruct:free' o 'deepseek/deepseek-chat')."
            elif "401" in err_msg or "unauthorized" in err_msg.lower() or "invalid api key" in err_msg.lower():
                help_tip = " -> Clave de API no válida o revocada."
            elif "402" in err_msg or "insufficient_quota" in err_msg.lower() or "credits" in err_msg.lower():
                help_tip = " -> Saldo insuficiente en la cuenta. Usa un modelo con sufijo ':free' en OpenRouter."

            return {
                "service": f"IA Personalizada ({clean_model})",
                "status": False,
                "message": f"Error: {err_msg}{help_tip}",
                "latency": round(elapsed, 2)
            }


    @staticmethod
    def test_pexels(api_key: str) -> Dict[str, Any]:
        """Prueba la API de Pexels para stock de video."""
        if not api_key:
            return {"service": "Pexels Video Stock", "status": False, "message": "Clave no configurada.", "latency": 0}
        
        t0 = time.time()
        try:
            url = "https://api.pexels.com/videos/search?query=ocean&orientation=portrait&per_page=1"
            headers = {"Authorization": api_key}
            res = requests.get(url, headers=headers, timeout=10)
            elapsed = time.time() - t0
            if res.status_code == 200:
                data = res.json()
                total = data.get("total_results", 0)
                return {
                    "service": "Pexels Video Stock",
                    "status": True,
                    "message": f"Conexión exitosa. Acceso verificado a millones de videos HD ({total} encontrados en test).",
                    "latency": round(elapsed, 2)
                }
            else:
                return {
                    "service": "Pexels Video Stock",
                    "status": False,
                    "message": f"Código HTTP {res.status_code}: Clave de API inválida o rechazada.",
                    "latency": round(elapsed, 2)
                }
        except Exception as e:
            elapsed = time.time() - t0
            return {"service": "Pexels Video Stock", "status": False, "message": f"Error: {str(e)}", "latency": round(elapsed, 2)}

    @staticmethod
    def test_edge_tts() -> Dict[str, Any]:
        """Prueba la síntesis de voz gratuita de Edge-TTS."""
        import asyncio, edge_tts
        t0 = time.time()
        try:
            async def run():
                comm = edge_tts.Communicate("Prueba de voz", voice="es-MX-JorgeNeural")
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        return True
                return False
            asyncio.run(run())
            elapsed = time.time() - t0
            return {
                "service": "Edge-TTS (Voces Neuronales)",
                "status": True,
                "message": "Servicio 100% operativo (Gratuito y sin límites de API).",
                "latency": round(elapsed, 2)
            }
        except Exception as e:
            elapsed = time.time() - t0
            return {"service": "Edge-TTS (Voces Neuronales)", "status": False, "message": f"Error: {str(e)}", "latency": round(elapsed, 2)}

    @staticmethod
    def test_pollinations() -> Dict[str, Any]:
        """Prueba la generación de imágenes IA con Pollinations (Flux)."""
        t0 = time.time()
        try:
            url = "https://image.pollinations.ai/prompt/test_circle?width=64&height=64&model=flux&nologo=true"
            res = requests.get(url, timeout=15)
            elapsed = time.time() - t0
            if res.status_code == 200 and len(res.content) > 500:
                return {
                    "service": "Pollinations AI (Flux Images)",
                    "status": True,
                    "message": "Servicio de generación de imágenes IA operativo (Gratuito).",
                    "latency": round(elapsed, 2)
                }
            else:
                return {
                    "service": "Pollinations AI (Flux Images)",
                    "status": False,
                    "message": f"Código HTTP {res.status_code} inesperado.",
                    "latency": round(elapsed, 2)
                }
        except Exception as e:
            elapsed = time.time() - t0
            return {"service": "Pollinations AI (Flux Images)", "status": False, "message": f"Error de conexión: {str(e)}", "latency": round(elapsed, 2)}
