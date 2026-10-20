"""
Script de Diagnóstico de APIs
Verifica rápidamente el estado, validez y velocidad de respuesta de todas las APIs configuradas.

Uso:
  python check_apis.py
"""

import os
import sys

# Configurar codificación segura
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def load_dotenv_custom():
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

load_dotenv_custom()

from src.core.api_checker import APIChecker

def print_result(res):
    status_icon = "✅" if res["status"] else "❌"
    print(f"\n{status_icon} [{res['service']}] (Tiempo: {res['latency']}s)")
    print(f"   Detalle: {res['message']}")

def main():
    print("=" * 60)
    print("🔍 DIAGNÓSTICO DE CONECTIVIDAD DE APIS - FÁBRICA DE REELS")
    print("=" * 60)

    # 1. Servicios Base Gratuitos
    print("\n📦 Probando servicios integrados gratuitos...")
    print_result(APIChecker.test_edge_tts())
    print_result(APIChecker.test_pollinations())

    # 2. APIs de LLM
    print("\n🧠 Probando APIs de Guiones e Inteligencia Artificial...")
    
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    custom_url = os.getenv("CUSTOM_LLM_BASE_URL", "")
    custom_key = os.getenv("CUSTOM_LLM_API_KEY", "")
    custom_model = os.getenv("CUSTOM_LLM_MODEL", "deepseek-chat")
    pexels_key = os.getenv("PEXELS_API_KEY", "")

    if gemini_key:
        print_result(APIChecker.test_gemini(gemini_key))
    else:
        print("\n⚪ [Google Gemini]: No configurado en .env (Opcional)")

    if openai_key:
        print_result(APIChecker.test_openai(openai_key))
    else:
        print("⚪ [OpenAI]: No configurado en .env (Opcional)")

    if groq_key:
        print_result(APIChecker.test_groq(groq_key))
    else:
        print("⚪ [Groq Cloud]: No configurado en .env (Opcional)")

    if custom_key or "localhost" in custom_url:
        print_result(APIChecker.test_custom_llm(custom_url, custom_key, custom_model))
    else:
        print("⚪ [IA Personalizada / DeepSeek]: No configurado en .env (Opcional)")

    # 3. Stock de Video
    print("\n🎥 Probando APIs de Video Stock...")
    if pexels_key:
        print_result(APIChecker.test_pexels(pexels_key))
    else:
        print("⚪ [Pexels Stock]: No configurado en .env (Opcional)")

    print("\n" + "=" * 60)
    print("🎯 DIAGNÓSTICO FINALIZADO")
    print("=" * 60)

if __name__ == "__main__":
    main()
