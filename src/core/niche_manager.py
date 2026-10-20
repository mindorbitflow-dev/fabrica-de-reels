"""
Niche Manager Module
Gestiona los nichos predeterminados y permite crear, editar y guardar nichos personalizados.
"""

import os
import warnings
import yaml
from typing import Dict, Any, List, Optional

DEFAULT_NICHES = {
    "curiosidades": {
        "name": "🧠 Curiosidades & Hechos Asombrosos",
        "description": "Datos impactantes que detienen el scroll en los primeros 2 segundos.",
        "tone": "Enérgico, intrigante, con preguntas abiertas y ritmo rápido.",
        "visual_style": "ultra-photorealistic 8k, cinematic dramatic lighting, macro perspective, vivid contrasting colors, vertical 9:16 composition, Unreal Engine 5 render style",
        "recommended_voice": "es-MX-JorgeNeural",
        "suggested_music": "energetic_beat",
        "keywords": ["sabías que", "increíble", "secreto", "descubrimiento"]
    },
    "psicologia_oscura": {
        "name": "👁️ Psicología Oscura & Mente Humana",
        "description": "Trucos mentales, lenguaje corporal y secretos de persuasión.",
        "tone": "Misterioso, persuasivo, revelador, con tono confidencial.",
        "visual_style": "dark moody cinematic atmosphere, noir lighting, chiaroscuro shadows, high contrast neon accents, vertical 9:16 wallpaper, photorealistic 8k",
        "recommended_voice": "es-ES-AlvaroNeural",
        "suggested_music": "dark_ambient",
        "keywords": ["psicología", "truco mental", "manipulación", "lenguaje corporal"]
    },
    "finanzas": {
        "name": "💰 Finanzas, Riqueza & Negocios",
        "description": "Hábitos de dinero, inversiones, interés compuesto y mentalidad de éxito.",
        "tone": "Autoritario, inspirador, práctico y directo al grano.",
        "visual_style": "sleek luxury aesthetic, golden hour cinematic glow, modern high-tech fintech charts, dark marble and gold reflections, vertical 9:16, masterpiece",
        "recommended_voice": "es-MX-JorgeNeural",
        "suggested_music": "modern_synth",
        "keywords": ["dinero", "inversión", "riqueza", "hábitos"]
    },
    "motivacion": {
        "name": "🏛️ Motivación & Filosofía Estoica",
        "description": "Disciplina, superación personal y sabiduría de Marco Aurelio y Séneca.",
        "tone": "Profundo, inspirador, reflexivo y de máxima fuerza mental.",
        "visual_style": "heroic cinematic lighting, ancient marble statues of stoic philosophers, dramatic sunrise over mountain peak, film grain texture, vertical 9:16",
        "recommended_voice": "es-AR-TomasNeural",
        "suggested_music": "epic_orchestral",
        "keywords": ["disciplina", "fuerza mental", "estoicismo", "superación"]
    },
    "misterio": {
        "name": "👻 Misterio & Terror Psicológico",
        "description": "Enigmas sin resolver, desapariciones, conspiraciones y leyendas oscuras.",
        "tone": "Suspenso creciente, tono sombrío, pausas estratégicas y giros inesperados.",
        "visual_style": "eerie cinematic horror atmosphere, heavy fog and dim volumetric lighting, VHS film grain, unsettling cinematic angle, vertical 9:16",
        "recommended_voice": "es-CO-GonzaloNeural",
        "suggested_music": "creepy_ambient",
        "keywords": ["misterio", "inexplicable", "terror", "desaparición"]
    },
    "ciencia": {
        "name": "🌌 Ciencia, Cosmos & Paradojas",
        "description": "Agujeros negros, física cuántica, viajes en el tiempo y el fin del universo.",
        "tone": "Fascinante, científico pero accesible, de asombro cósmico.",
        "visual_style": "jaw-dropping cosmic nebula, glowing event horizon of supermassive black hole, quantum particles in deep space, hyper-detailed 8k, vertical 9:16",
        "recommended_voice": "es-ES-ElviraNeural",
        "suggested_music": "space_ambient",
        "keywords": ["universo", "física", "agujero negro", "paradoja"]
    },
    "historia_oculta": {
        "name": "📜 Historia Oculta & Civilizaciones",
        "description": "Secretos de Egipto, Roma, pirámides, artefactos y batallas olvidadas.",
        "tone": "Documental épico, revelador, desmintiendo mitos históricos.",
        "visual_style": "ancient epic civilization cinematography, golden torches inside Egyptian tomb, photorealistic historical artifact, vertical 9:16, 8k",
        "recommended_voice": "es-ES-AlvaroNeural",
        "suggested_music": "historical_epic",
        "keywords": ["historia", "pirámides", "antiguo", "secreto"]
    },
    "tecnologia_ia": {
        "name": "🤖 Inteligencia Artificial & Futuro",
        "description": "Avances de IA, robótica, cibernética y el impacto en la humanidad.",
        "tone": "Futurista, disruptivo, de alta expectativa y tecnología de punta.",
        "visual_style": "cyberpunk futuristic Tokyo aesthetic, glowing holographic UI, humanoid robot with glowing neural networks, vertical 9:16, 8k render",
        "recommended_voice": "es-MX-DaliaNeural",
        "suggested_music": "cyberpunk_synth",
        "keywords": ["IA", "futuro", "tecnología", "robots"]
    },
    "salud_biohacking": {
        "name": "🏋️ Salud, Biohacking & Longevidad",
        "description": "Optimización del sueño, nutrición celular, ayuno y energía diaria.",
        "tone": "Práctico, basado en ciencia, transformador y motivante.",
        "visual_style": "clean modern biohacking aesthetic, bright natural cinematic sunlight, DNA double helix digital visualization, vertical 9:16",
        "recommended_voice": "es-CO-SalomeNeural",
        "suggested_music": "chill_lofi",
        "keywords": ["salud", "energía", "sueño", "longevidad"]
    },
    "documental_history": {
        "name": "🏺 Documental History Channel (Enigmas & Archivos Prohibidos)",
        "description": "Investigaciones cinematográficas estilo History Channel: misterios arqueológicos, tecnologías ancestrales, archivos desclasificados y conspiraciones históricas.",
        "tone": "Solemne, profundo, dramático y de máxima intriga investigativa. Usa el clásico cuestionamiento del narrador de cable ('¿Y si la historia oficial estuviera equivocada? ¿Qué evidencia se ocultó realmente?'), pausas estratégicas de suspenso y datos documentados.",
        "visual_style": "cinematic History Channel documentary footage, ancient monolithic temples under stormy sky, golden volumetric god rays piercing dusty archaeological excavation, aged parchment maps with glowing coordinates, 35mm film texture, slow dramatic 3D drone sweep over ancient ruins, photorealistic 8k, IMAX documentary quality",
        "recommended_voice": "es-ES-AlvaroNeural",
        "suggested_music": "historical_epic",
        "keywords": ["documental", "arqueología", "historia oculta", "archivos desclasificados", "civilizaciones perdidas", "enigma"]
    }
}


class NicheManager:
    def __init__(self, niches_file: str = "config/niches.yaml"):
        self.niches_file = niches_file
        self.niches = self._load_niches()

    def _load_niches(self) -> Dict[str, Any]:
        """Carga los nichos desde disco o inicializa con los predeterminados."""
        if os.path.exists(self.niches_file):
            try:
                with open(self.niches_file, "r", encoding="utf-8") as f:
                    saved = yaml.safe_load(f)
                    if isinstance(saved, dict) and saved:
                        migrated = False
                        for key, niche in saved.items():
                            if isinstance(niche, dict) and "style_prompt" in niche and "visual_style" not in niche:
                                niche["visual_style"] = niche.pop("style_prompt")
                                migrated = True
                        if migrated:
                            warnings.warn(
                                "Se migró style_prompt a visual_style en la configuración de nichos.",
                                stacklevel=2,
                            )
                            self._save_to_disk(saved)
                        # Combinar con los por defecto para asegurar que no falten
                        merged = DEFAULT_NICHES.copy()
                        merged.update(saved)
                        return merged
            except Exception as e:
                print(f"[NicheManager] Error cargando {self.niches_file}: {e}")
        
        # Si no existe archivo, guardar los predeterminados
        self._save_to_disk(DEFAULT_NICHES)
        return DEFAULT_NICHES.copy()

    def get_all_niches(self) -> Dict[str, Any]:
        return self.niches

    def list_niches(self) -> Dict[str, Any]:
        """Alias para listar todos los nichos disponibles."""
        return self.get_all_niches()

    def get_niche(self, key: str) -> Dict[str, Any]:
        return self.niches.get(key.lower(), self.niches.get("curiosidades", DEFAULT_NICHES["curiosidades"]))

    def add_or_update_niche(
        self,
        key: str,
        name: str,
        description: str,
        tone: str,
        visual_style: str,
        recommended_voice: str = "es-MX-JorgeNeural",
        keywords: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Crea o actualiza un nicho personalizado y lo persiste en disco."""
        clean_key = "".join(c for c in key.lower() if c.isalnum() or c == "_").strip()
        if not clean_key:
            clean_key = "nicho_personalizado"

        niche_data = {
            "name": name,
            "description": description,
            "tone": tone,
            "visual_style": visual_style,
            "recommended_voice": recommended_voice,
            "keywords": keywords or [clean_key]
        }

        self.niches[clean_key] = niche_data
        self._save_to_disk(self.niches)
        return niche_data

    def delete_niche(self, key: str) -> bool:
        """Elimina un nicho si existe."""
        if key in self.niches:
            del self.niches[key]
            self._save_to_disk(self.niches)
            return True
        return False

    def _save_to_disk(self, data: Dict[str, Any]):
        os.makedirs(os.path.dirname(os.path.abspath(self.niches_file)), exist_ok=True)
        with open(self.niches_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
