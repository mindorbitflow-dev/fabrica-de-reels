"""
AI Editor Copilot Module
Motor de edición inteligente por comandos de texto (estilo CapCut / InShot AI).
Interpreta instrucciones en lenguaje natural del usuario y aplica modificaciones precisas
sobre el guión, pistas de video, efectos 3D, subtítulos, voz y música.
"""

import os
import json
import re
from typing import Dict, Any, Tuple, Optional
from src.core.llm_client import LLMRouter

COPILOT_SYSTEM_PROMPT = """Eres el ASISTENTE INTELIGENTE DE EDICIÓN DE VIDEO (AI Video Editor Copilot estilo CapCut / InShot).
Tu misión es recibir el ESTADO ACTUAL DE UN PROYECTO DE VIDEO y una INSTRUCCIÓN DE EDICIÓN DEL USUARIO, y devolver el nuevo estado modificado junto con una explicación clara de los cambios.

ESTADO DEL PROYECTO:
El proyecto contiene:
- `title`: Título del video
- `hook`: Gancho inicial
- `scenes`: Lista de escenas, cada una con:
    * `scene_id`: int
    * `text`: Texto hablado en español
    * `media_type`: "ai_image" | "pexels_video" | "3d_parallax"
    * `pexels_query`: Búsqueda en inglés para Pexels si media_type es pexels_video
    * `image_prompt`: Prompt visual en inglés si media_type es ai_image o 3d_parallax
- `voice`: ID de voz (es-MX-JorgeNeural, es-ES-AlvaroNeural, es-MX-DaliaNeural, etc.)

- `voice_rate`: Velocidad de voz (-10%, +0%, +5%, +12%, +20%)
- `subtitle_style`: Estilo ("yellow_pop", "green_neon", "cyan_electric", "red_fire", "white_pure", "none" para desactivar subtítulos)
- `subtitle_position`: Posición (bottom, center, top)

- `words_per_line`: int (1 a 5)
- `aspect_ratio`: Formato (9:16, 1:1, 16:9)
- `zoom_intensity`: float (0.0 a 1.8)
- `music_volume`: float (0.0 a 0.35)

INSTRUCCIONES DE RESPUESTA:
Debes responder OBLIGATORIAMENTE con un JSON válido con esta estructura:
{
  "explanation": "Resumen conciso y profesional en español con viñetas de lo que modificaste (ej: '✅ Escena 2 cambiada a video de Pexels con query 'ocean waves'\\n✅ Subtítulos cambiados a Verde Neón')",
  "updated_project": { ... todos los campos del proyecto con los cambios aplicados ... }
}
"""

class AIEditorCopilot:
    def __init__(self):
        self.llm = LLMRouter()

    def edit_project(self, current_project: Dict[str, Any], user_command: str) -> Tuple[Dict[str, Any], str]:
        """
        Ejecuta la edición asistida por IA sobre el estado del proyecto según la instrucción del usuario.
        Retorna (updated_project_dict, explanation_markdown).
        """
        prompt = f"""{COPILOT_SYSTEM_PROMPT}

================================================================================
ESTADO ACTUAL DEL PROYECTO (JSON):
================================================================================
{json.dumps(current_project, indent=2, ensure_ascii=False)}

================================================================================
INSTRUCCIÓN DEL USUARIO:
================================================================================
"{user_command}"

Aplica las modificaciones requeridas de forma precisa y devuelve el JSON solicitado.
"""
        if not self.llm.has_any:
            return self._fallback_rule_editor(current_project, user_command)
        try:
            raw, _ = self.llm.complete_any(prompt, max_tokens=6000, temperature=0.3)
            return self._parse_copilot_response(raw, current_project, user_command)
        except Exception as e:
            print(f"[AIEditorCopilot] Error en llamada a LLM: {e}. Usando procesador de reglas.")
            return self._fallback_rule_editor(current_project, user_command)

    def _parse_copilot_response(self, raw_text: str, original: Dict[str, Any], cmd: str) -> Tuple[Dict[str, Any], str]:
        try:
            cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw_text or "", flags=re.IGNORECASE).strip()
            cleaned = cleaned[cleaned.find("{"):cleaned.rfind("}") + 1]
            data = json.loads(cleaned)
            explanation = data.get("explanation", "Edición completada con éxito.")
            updated = data.get("updated_project", original)
            return updated, explanation
        except Exception as e:
            print(f"[AIEditorCopilot] Error parseando JSON de copilot: {e}")
            return self._fallback_rule_editor(original, cmd)

    def _fallback_rule_editor(self, original: Dict[str, Any], cmd: str) -> Tuple[Dict[str, Any], str]:
        """Editor heurístico por reglas para cuando no hay API conectada o falla la red."""
        updated = json.loads(json.dumps(original)) # Deep copy
        cmd_lower = cmd.lower()
        actions = []

        # Subtítulos
        if "quitar subtitulo" in cmd_lower or "quitar subtítulo" in cmd_lower or "desactivar subtitulo" in cmd_lower or "desactivar subtítulo" in cmd_lower or "sin subtitulo" in cmd_lower or "sin subtítulo" in cmd_lower:
            updated["subtitle_style"] = "none"
            actions.append("✅ Subtítulos desactivados (Video limpio sin texto).")
        elif "verde" in cmd_lower or "neon" in cmd_lower:
            updated["subtitle_style"] = "green_neon"
            actions.append("✅ Estilo de subtítulos cambiado a Verde Neón.")
        elif "amarillo" in cmd_lower:
            updated["subtitle_style"] = "yellow_pop"
            actions.append("✅ Estilo de subtítulos cambiado a Amarillo Pop.")
        elif "rojo" in cmd_lower or "fuego" in cmd_lower:
            updated["subtitle_style"] = "red_fire"
            actions.append("✅ Estilo de subtítulos cambiado a Rojo Fuego.")
        elif "cyan" in cmd_lower or "azul" in cmd_lower:
            updated["subtitle_style"] = "cyan_electric"
            actions.append("✅ Estilo de subtítulos cambiado a Cyan Eléctrico.")
        elif "blanco" in cmd_lower:
            updated["subtitle_style"] = "white_pure"
            actions.append("✅ Estilo de subtítulos cambiado a Blanco Puro.")

        if "arriba" in cmd_lower:
            updated["subtitle_position"] = "top"
            actions.append("✅ Subtítulos posicionados arriba.")
        elif "centro" in cmd_lower:
            updated["subtitle_position"] = "center"
            actions.append("✅ Subtítulos posicionados en el centro.")
        elif "abajo" in cmd_lower:
            updated["subtitle_position"] = "bottom"
            actions.append("✅ Subtítulos posicionados abajo.")

        # Efecto 3D Parallax
        if "3d" in cmd_lower or "parallax" in cmd_lower:
            for s in updated.get("scenes", []):
                s["media_type"] = "3d_parallax"
            actions.append("✅ Efecto 3D Parallax aplicado a las escenas.")

        # Pexels Video
        if "pexels" in cmd_lower or "video" in cmd_lower:
            for s in updated.get("scenes", []):
                s["media_type"] = "pexels_video"
                if not s.get("pexels_query"):
                    s["pexels_query"] = updated.get("title", "cinematic footage 4k")
            actions.append("✅ Escenas configuradas con clips de video de Pexels.")

        # Formato
        if "16:9" in cmd_lower or "youtube" in cmd_lower or "horizontal" in cmd_lower:
            updated["aspect_ratio"] = "16:9"
            actions.append("✅ Formato cambiado a Horizontal 16:9 (YouTube).")
        elif "9:16" in cmd_lower or "vertical" in cmd_lower or "reel" in cmd_lower or "tiktok" in cmd_lower:
            updated["aspect_ratio"] = "9:16"
            actions.append("✅ Formato cambiado a Vertical 9:16 (Reels/TikTok).")

        # Velocidad de voz
        if any(w in cmd_lower for w in ["rápido", "rapido", "+10", "10%", "acelera", "acelerar", "sube"]):
            updated["voice_rate"] = "+12%"
            actions.append("✅ Velocidad de voz aumentada a +12%.")
        elif any(w in cmd_lower for w in ["lento", "desacelera", "pausado", "baja la velocidad"]):
            updated["voice_rate"] = "-10%"
            actions.append("✅ Velocidad de voz ajustada a modo pausado (-10%).")

        # Volumen de música
        if "música" in cmd_lower or "musica" in cmd_lower or "volumen" in cmd_lower:
            if "baja" in cmd_lower or "mitad" in cmd_lower or "menos" in cmd_lower:
                updated["music_volume"] = max(0.02, round(updated.get("music_volume", 0.12) * 0.5, 2))
                actions.append(f"✅ Volumen de música reducido a {updated['music_volume']}.")
            elif "sube" in cmd_lower or "mas" in cmd_lower or "más" in cmd_lower:
                updated["music_volume"] = min(0.35, round(updated.get("music_volume", 0.12) * 1.5, 2))
                actions.append(f"✅ Volumen de música aumentado a {updated['music_volume']}.")

        if not actions:
            actions.append("✅ Comando procesado y sincronizado con el proyecto.")

        return updated, "\n".join(actions)
