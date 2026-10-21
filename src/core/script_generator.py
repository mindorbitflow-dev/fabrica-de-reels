"""
Script Generator Module
Genera guiones estructurados para Reels/TikTok/Shorts.
Soporta: proveedor OpenAI-compatible (OpenRouter, DeepSeek...), Gemini, OpenAI, Groq
y un modo offline que respeta el contrato de duración.
"""

import json
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict, field, fields

from src.core.director_skills import CREATIVE_DIRECTOR_SYSTEM_PROMPT, build_director_prompt, build_post_prompt
from src.core.llm_client import LLMRouter, LLMUnavailableError
from src.core.niche_manager import NicheManager
from src.core.production import SHOT_INTENTS
from src.core.topic_picker import TopicPicker, is_random_topic
from src.core.script_quality import (
    RELAXED_DURATION_TOLERANCE,
    WORDS_PER_SECOND_BY_LANGUAGE,
    assess_script,
    count_words,
    create_script_plan,
)


@dataclass
class Scene:
    scene_id: int
    text: str
    image_prompt: str
    keywords: List[str]
    media_type: str = "ai_image"              # "ai_image", "pexels_video", "3d_parallax"
    pexels_query: str = ""                    # Búsqueda en inglés para Pexels si media_type es pexels_video
    camera_motion: str = "fast_punch_zoom"    # "fast_punch_zoom", "slow_dolly_in", "slow_pull_out", "pan_left", "pan_right", "camera_shake", "static_cinematic"
    transition_out: str = "smash_cut"         # "smash_cut", "crossfade", "whip_pan_right", "whip_pan_left", "flash_white", "glitch", "zoom_in"
    visual_vfx: str = "cinematic_clean"       # "cinematic_clean", "deep_vignette", "film_grain", "teal_orange", "noir_bw", "warm_vintage"
    sfx_cue: str = "none"                     # "impact_boom", "whoosh_fast", "cinematic_riser", "sub_bass_drop", "glitch_sfx", "none"
    subtitle_mode: str = "dynamic_pop"        # "single_word_punch", "dynamic_pop", "fluid_flow"
    accent_color: str = "&H0000E5FF&"         # Color ASS BGR para resaltar la palabra clave
    broll: List[str] = field(default_factory=list)   # Búsquedas de stock en inglés, una por frase


@dataclass
class ReelScript:
    title: str
    niche: str
    hook: str
    scenes: List[Scene]
    cta: str
    full_script_text: str
    caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    source: str = "offline"                   # Proveedor que escribió el guion
    language: str = "es"
    sources: List[str] = field(default_factory=list)   # Fuentes del dossier (documentales)
    on_screen_title: str = ""                 # Título corto que se ve en los primeros segundos

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReelScript":
        scene_fields = {f.name for f in fields(Scene)}
        scenes = [Scene(**{k: v for k, v in scene.items() if k in scene_fields}) for scene in data.get("scenes", [])]
        return cls(
            title=data.get("title", "Video"),
            niche=data.get("niche", "curiosidades"),
            hook=data.get("hook", ""),
            scenes=scenes,
            cta=data.get("cta", ""),
            full_script_text=data.get("full_script_text", ""),
            caption=data.get("caption", ""),
            hashtags=list(data.get("hashtags", []) or []),
            source=data.get("source", "offline"),
            language=data.get("language", "es"),
            sources=list(data.get("sources", []) or []),
            on_screen_title=data.get("on_screen_title", ""),
        )


SYSTEM_PROMPT = CREATIVE_DIRECTOR_SYSTEM_PROMPT

VALID_MEDIA = ("ai_image", "pexels_video", "3d_parallax")
VALID_CAMERAS = ("fast_punch_zoom", "slow_dolly_in", "slow_pull_out", "pan_left", "pan_right", "camera_shake", "static_cinematic")
VALID_TRANSITIONS = ("smash_cut", "crossfade", "whip_pan_right", "whip_pan_left", "flash_white", "glitch", "zoom_in")
VALID_VFX = ("cinematic_clean", "deep_vignette", "film_grain", "teal_orange", "noir_bw", "warm_vintage")
VALID_SFX = ("impact_boom", "whoosh_fast", "cinematic_riser", "sub_bass_drop", "glitch_sfx", "none")
VALID_SUB_MODES = ("single_word_punch", "dynamic_pop", "fluid_flow")
ASS_COLOR_RE = re.compile(r"^&H[0-9A-Fa-f]{6,8}&?$")


class ScriptGenerator:
    # Intentos por proveedor: el primero normal y uno con feedback del contrato.
    ATTEMPTS_PER_PROVIDER = 2

    def __init__(self, preferred_provider: str = "auto"):
        self.llm = LLMRouter()
        self.niche_manager = NicheManager()
        self.preferred_provider = preferred_provider
        self.topics = TopicPicker(suggest=lambda niche, count, avoid: self.suggest_topics(niche, count, avoid, fallback=False))

    def generate(
        self,
        topic: str,
        niche: str = "curiosidades",
        provider: str = "auto",
        target_duration: int = 30,
        scene_count: Optional[int] = None,
        platform: Optional[str] = None,
        research_brief: Optional[str] = None,
        voice_engine: str = "edge",
        **kwargs
    ) -> ReelScript:
        """
        Genera un guion con el primer proveedor disponible que cumpla el contrato editorial.
        Si un guion se sale del presupuesto, se pide una corrección con el problema concreto.
        """
        if provider == "auto":
            provider = self.preferred_provider
        if is_random_topic(topic):
            topic = self.topics.pick(niche)
            print(f"[ScriptGenerator] Tema aleatorio elegido para '{niche}': {topic}")
        niche_data = self.niche_manager.get_niche(niche)
        plan = create_script_plan(target_duration, scene_count, engine=voice_engine)
        base_prompt = build_director_prompt(topic, niche_data, target_duration, scene_count,
                                            platform=platform, research_brief=research_brief,
                                            voice_engine=voice_engine)
        max_tokens = min(8192, max(2500, int(plan.target_words * 12)))
        timeout = max(60.0, min(180.0, target_duration * 0.4))

        best_relaxed: Optional[ReelScript] = None
        for provider_name in self.llm.available_providers(provider):
            prompt = base_prompt
            for attempt in range(self.ATTEMPTS_PER_PROVIDER):
                try:
                    raw = self.llm.complete(prompt, provider_name, max_tokens=max_tokens, timeout=timeout)
                    script = self._parse_llm_json(raw, topic, niche, target_duration, scene_count)
                except Exception as error:
                    self.llm.handle_error(provider_name, error)
                    break  # Falla de red/credenciales/formato: siguiente proveedor
                script.source = provider_name

                assessment = assess_script(script.full_script_text, (s.text for s in script.scenes), plan)
                if assessment.strict_ok:
                    return script

                relaxed = assess_script(
                    script.full_script_text, (s.text for s in script.scenes), plan,
                    tolerance=RELAXED_DURATION_TOLERANCE,
                )
                if (relaxed.duration_ok and not relaxed.duplicate_scene_count
                        and not relaxed.forced_editorial_pattern_count and best_relaxed is None):
                    best_relaxed = script

                print(f"[ScriptGenerator] {provider_name} intento {attempt + 1}: fuera de contrato: " + " ".join(assessment.issues))
                prompt = (
                    base_prompt
                    + "\n\nCORRECCIÓN OBLIGATORIA: tu respuesta anterior no cumplió el encargo. Problemas: "
                    + " ".join(assessment.issues)
                    + f" Reescribe el guion completo con exactamente {plan.scene_count} escenas y entre "
                    + f"{plan.min_words} y {plan.max_words} palabras en total."
                )

        if best_relaxed is not None:
            print("[ScriptGenerator] Se acepta el guion más cercano al presupuesto; la duración final se mide sobre el audio real.")
            return best_relaxed

        if self.llm.has_any:
            print("[ScriptGenerator] Ningún proveedor entregó un guion válido. Usando generador offline.")
        return self._generate_offline(topic, niche, niche_data, target_duration, scene_count)

    def script_from_facts(self, topic: str, niche: str, facts: List[str], target_duration: int = 60,
                          scene_count: Optional[int] = None) -> ReelScript:
        """Documental sin LLM: cada escena narra un hecho verificado del dossier, en orden."""
        plan = create_script_plan(target_duration, scene_count)
        usable = [f.strip() for f in facts if f.strip()]
        if not usable:
            return self._generate_offline(topic, niche, self.niche_manager.get_niche(niche), target_duration, scene_count)
        hook = f"{topic}: esto es lo que cuentan las fuentes."
        chosen, words = [], len(hook.split())
        for fact in usable:
            if len(chosen) >= plan.scene_count or words >= plan.target_words:
                break
            chosen.append(fact)
            words += len(fact.split())
        motions = ["slow_dolly_in", "pan_left", "slow_pull_out", "pan_right", "fast_punch_zoom"]
        scenes = [Scene(
            scene_id=i + 1, text=fact,
            image_prompt=f"{topic}, historical documentary photograph, {SHOT_INTENTS[i % len(SHOT_INTENTS)]}, well lit",
            keywords=[topic.split()[0]], media_type="ai_image", pexels_query=topic,
            camera_motion=motions[i % len(motions)], transition_out="crossfade" if i % 2 else "smash_cut",
            sfx_cue="cinematic_riser" if i == 0 else "none", subtitle_mode="dynamic_pop",
        ) for i, fact in enumerate(chosen)]
        keywords = self.niche_manager.get_niche(niche).get("keywords", [])
        return ReelScript(
            title=topic, niche=niche, hook=hook, scenes=scenes, cta="",
            full_script_text=" ".join([hook] + chosen), caption=hook,
            hashtags=_hashtags_from(keywords[:1] + ["Documental"] + ([topic] if len(topic) <= 24 else [])), source="dossier",
        )

    def generate_post(self, topic: str, niche: str, research_brief: Optional[str] = None,
                      preferred: Optional[str] = None) -> Dict[str, Any]:
        """Texto de un post de imagen. Sin IA devuelve un post sobrio con source='offline'."""
        niche_data = self.niche_manager.get_niche(niche)
        prompt = build_post_prompt(topic, niche_data, research_brief)
        for provider_name in self.llm.available_providers(preferred or self.preferred_provider):
            try:
                data = self._repair_and_load_json(self.llm.complete(prompt, provider_name, max_tokens=1500, timeout=90)) or {}
            except Exception as error:
                self.llm.handle_error(provider_name, error)
                continue
            headline = re.sub(r"\s+", " ", str(data.get("headline", ""))).strip()
            caption = str(data.get("caption", "")).strip()
            if 3 <= count_words(headline) <= 12 and count_words(caption) >= 30:
                tags = [str(t).strip() for t in (data.get("hashtags") or []) if str(t).strip()]
                return {
                    "title": str(data.get("title") or headline).strip(),
                    "headline": headline,
                    "highlight": _as_list(data.get("highlight"))[:2],
                    "kicker": str(data.get("kicker") or "").strip()[:28],
                    "caption": caption,
                    "hashtags": [t if t.startswith("#") else "#" + t for t in tags][:8],
                    "image_prompt": str(data.get("image_prompt") or f"photorealistic photograph of {topic}").strip(),
                    "pexels_query": str(data.get("pexels_query") or "").strip(),
                    "source": provider_name,
                }
            print(f"[ScriptGenerator] {provider_name}: post fuera de formato (titular {count_words(headline)} palabras, texto {count_words(caption)}).")
        keywords = niche_data.get("keywords", [])
        return {"title": topic, "headline": topic, "highlight": [], "kicker": "", "caption": topic, "hashtags": _hashtags_from(keywords),
                "image_prompt": f"photorealistic documentary photograph of {topic}, well lit", "pexels_query": topic,
                "source": "offline"}

    def adapt(self, script: ReelScript, language: str = "en", platform: Optional[str] = None) -> ReelScript:
        """Adapta (no traduce literal) el guion a otro idioma conservando escenas y dirección visual.

        La duración se mantiene: el presupuesto de palabras usa el ritmo de la voz del idioma destino."""
        target_seconds = round(count_words(script.full_script_text) / WORDS_PER_SECOND_BY_LANGUAGE.get(script.language, 2.2))
        plan = create_script_plan(target_seconds, len(script.scenes), language)
        lang_name = {"en": "inglés (Estados Unidos)", "es": "español latinoamericano"}.get(language, language)
        payload = {
            "title": script.title, "hook": script.hook, "caption": script.caption,
            "scenes": [{"scene_id": s.scene_id, "text": s.text} for s in script.scenes],
        }
        prompt = (
            f"Adapta este guion de video corto al {lang_name} como lo escribiría un creador nativo: natural, oral y "
            "con el mismo ritmo, sin traducir palabra por palabra. Conserva exactamente el mismo número de escenas y "
            "su orden, los mismos hechos y cifras, sin añadir nada.\n"
            f"Total entre {plan.min_words} y {plan.max_words} palabras (incluido el hook).\n"
            f"Añade caption y 5-8 hashtags pensados para {'TikTok' if platform == 'tiktok' else 'Instagram/Facebook Reels'} en ese idioma.\n"
            'Responde solo JSON: {"title": "", "hook": "", "caption": "", "hashtags": [], "scenes": [{"scene_id": 1, "text": ""}]}\n\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        raw, provider = self.llm.complete_any(prompt, preferred=self.preferred_provider, max_tokens=4000, temperature=0.5)
        data = self._repair_and_load_json(raw[raw.find("{"):]) if "{" in raw else None
        scenes_out = (data or {}).get("scenes") or []
        if len(scenes_out) != len(script.scenes) or not all(str(s.get("text", "")).strip() for s in scenes_out):
            raise ValueError("La adaptación no conservó las escenas del guion original.")
        adapted = ReelScript.from_dict(script.to_dict())
        for scene, item in zip(adapted.scenes, scenes_out):
            scene.text = str(item["text"]).strip()
        adapted.title = str(data.get("title") or script.title)
        adapted.hook = str(data.get("hook") or "").strip()
        adapted.caption = str(data.get("caption") or "").strip()
        adapted.hashtags = _hashtags_from(data.get("hashtags") or [])
        adapted.full_script_text = " ".join(t for t in [adapted.hook] + [s.text for s in adapted.scenes] if t)
        adapted.language = language
        adapted.source = provider
        return adapted

    def suggest_topics(self, niche: str, count: int = 5, avoid: Optional[List[str]] = None, fallback: bool = True) -> List[str]:
        """Propone temas concretos del nicho. Sin LLM: temas del banco curado (o [] si fallback=False)."""
        niche_data = self.niche_manager.get_niche(niche)
        avoid = avoid or []
        prompt = (
            f"Propón {count} temas concretos y distintos para videos cortos del nicho "
            f"\"{niche_data.get('name', niche)}\" ({niche_data.get('description', '')}). "
            "Cada tema debe ser específico y verificable, no genérico ni sensacionalista. "
            + (f"No repitas estos temas: {'; '.join(avoid)}. " if avoid else "")
            + "Escribe cada tema en español como el título de un video (máximo 12 palabras). "
            + 'Responde solo con JSON: {"topics": ["tema 1", "tema 2"]}'
        )
        if not self.llm.available_providers(self.preferred_provider):
            return self._bank_topics(niche, count, avoid) if fallback else []
        try:
            raw, _ = self.llm.complete_any(prompt, preferred=self.preferred_provider, max_tokens=800, temperature=0.9)
            data = self._repair_and_load_json(raw[raw.find("{"):]) if "{" in raw else None
            topics = [t.strip() for t in (data or {}).get("topics", []) if isinstance(t, str) and t.strip()]
            if topics:
                return topics[:count]
        except (LLMUnavailableError, Exception) as error:
            print(f"[ScriptGenerator] No se pudieron sugerir temas con IA: {error}")
        return self._bank_topics(niche, count, avoid) if fallback else []

    def _bank_topics(self, niche: str, count: int, avoid: Optional[List[str]] = None) -> List[str]:
        from src.core.topic_picker import is_similar
        used = (avoid or []) + self.topics.used_topics(niche)
        fresh = [t for t in self.topics.bank(niche) if not any(is_similar(t, u) for u in used)]
        return fresh[:count]

    def _ensure_script_contract(
        self,
        script: ReelScript,
        topic: str,
        niche: str,
        niche_data: Dict[str, Any],
        target_duration: int,
        scene_count: Optional[int],
    ) -> ReelScript:
        """Evita que una respuesta válida en JSON llegue al render si no cabe en su duración."""
        plan = create_script_plan(target_duration, scene_count)
        assessment = assess_script(script.full_script_text, (scene.text for scene in script.scenes), plan)
        if assessment.strict_ok:
            return script
        print("[ScriptGenerator] Guion fuera de contrato editorial: " + " ".join(assessment.issues))
        return self._generate_offline(topic, niche, niche_data, target_duration, scene_count)

    @staticmethod
    def _trim_to_word_budget(text: str, budget: int) -> str:
        """Recorta solo el fallback para que su duración sea predecible."""
        words = text.split()
        if len(words) <= budget:
            return text
        trimmed = " ".join(words[:budget]).rstrip(" ,;:")
        return trimmed if trimmed.endswith((".", "?", "!")) else trimmed + "."

    def _generate_offline(
        self,
        topic: str,
        niche: str,
        niche_data: Optional[Dict[str, Any]] = None,
        target_duration: int = 30,
        scene_count: Optional[int] = None,
    ) -> ReelScript:
        """Fallback seguro, sobrio y con duración controlada.

        No simula investigación ni inventa datos sobre un tema arbitrario. Cuando no
        hay LLM disponible, entrega una explicación neutra que respeta el presupuesto
        de palabras y nunca repite escenas.
        """
        plan = create_script_plan(target_duration, scene_count)
        effective_topic = (topic or "este tema").strip() or "este tema"
        visual_style = (niche_data or {}).get("visual_style", "photorealistic cinematic lighting, vertical 9:16")
        hook = f"{effective_topic}: lo importante no siempre es lo más evidente."
        if count_words(hook) > plan.target_words // 3:
            hook = f"{effective_topic}."
        remaining_words = max(plan.scene_count * 3, plan.target_words - count_words(hook))
        base_budget, remainder = divmod(remaining_words, plan.scene_count)
        scene_budgets = [base_budget + (1 if index < remainder else 0) for index in range(plan.scene_count)]

        role_texts = [
            "Para entender {topic}, conviene separar los hechos de las historias que lo rodean.",
            "El contexto importa: una misma idea cambia según el momento, el lugar y las condiciones.",
            "La clave es mirar cómo funciona, no solo repetir el dato más llamativo.",
            "Un ejemplo lo aclara: cambiar una condición puede modificar el resultado esperado.",
            "También hay límites: una respuesta simple rara vez cubre todos los casos.",
            "Al comparar perspectivas aparecen matices que una versión rápida suele dejar fuera.",
            "Conviene distinguir causa, coincidencia y consecuencia antes de sacar conclusiones.",
            "La pregunta útil es qué evidencia confirmaría esta idea y qué la pondría en duda.",
            "Así, {topic} deja de ser una frase aislada y se puede observar mejor.",
            "La explicación gana claridad cuando se entiende el problema antes de buscar lo espectacular.",
        ]
        expansions = [
            "Eso permite avanzar sin llenar los vacíos con suposiciones.",
            "El detalle cambia la lectura más de lo que parece al principio.",
            "Mirarlo paso a paso suele ser más útil que buscar una respuesta inmediata.",
            "Así se entiende qué parte de la idea se sostiene y cuál necesita más contexto.",
            "No es menos interesante por ser más preciso; al contrario, se vuelve más claro.",
        ]
        short_expansions = ["Eso importa.", "El detalle cuenta.", "Ahí está la diferencia.", "Conviene recordarlo.", "Vale la pena notarlo."]
        motions = ["fast_punch_zoom", "slow_dolly_in", "pan_left", "static_cinematic", "slow_pull_out", "pan_right"]
        transitions = ["smash_cut", "crossfade", "whip_pan_right", "crossfade", "zoom_in", "smash_cut"]
        scenes = []
        for index, budget in enumerate(scene_budgets):
            text = role_texts[index % len(role_texts)].format(topic=effective_topic)
            if index >= len(role_texts):
                # El prefijo numerado garantiza que ninguna escena se repita en guiones largos.
                text = f"Punto {index + 1}: {text[0].lower()}{text[1:]}"
            text = self._trim_to_word_budget(text, budget)
            expansion_index = index
            while True:
                candidates = [
                    expansions[expansion_index % len(expansions)],
                    short_expansions[expansion_index % len(short_expansions)],
                ]
                addition = next((item for item in candidates if count_words(text + " " + item) <= budget), None)
                if not addition:
                    break
                text += " " + addition
                expansion_index += 1
            scenes.append(Scene(
                scene_id=index + 1,
                text=text,
                image_prompt=f"{effective_topic}, {SHOT_INTENTS[index % len(SHOT_INTENTS)]}, photorealistic documentary photograph, {_style_hint(visual_style)}",
                keywords=[effective_topic.split()[0], "contexto" if index == 0 else "explicación"],
                media_type="ai_image" if index % 3 == 2 else "3d_parallax",
                pexels_query=f"{effective_topic} documentary",
                camera_motion=motions[index % len(motions)],
                transition_out=transitions[index % len(transitions)],
                visual_vfx="cinematic_clean",
                sfx_cue="impact_boom" if index == 0 else "none",
                subtitle_mode="single_word_punch" if index == 0 else "dynamic_pop",
            ))

        full_text = f"{hook} " + " ".join(scene.text for scene in scenes)
        keywords = (niche_data or {}).get("keywords", [])
        return ReelScript(
            title=effective_topic,
            niche=niche,
            hook=hook,
            scenes=scenes,
            cta="",
            full_script_text=full_text,
            caption=hook,
            hashtags=_hashtags_from(keywords + ([effective_topic] if len(effective_topic) <= 24 else [])),
            source="offline",
        )

    def _repair_and_load_json(self, raw_str: str) -> Optional[Dict[str, Any]]:
        """Intenta parsear el JSON de forma normal y, si fue truncado por tokens, repara y recupera las escenas completas."""
        # 1. Intento directo si tiene cierre
        last_brace = raw_str.rfind("}")
        if last_brace != -1:
            candidate = raw_str[:last_brace + 1].strip()
            candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        # 2. Si fue cortado dentro del array 'scenes':
        scenes_match = re.search(r'"scenes"\s*:\s*\[', raw_str)
        if scenes_match:
            start_arr = scenes_match.end()
            curr_pos = raw_str.rfind("}")
            while curr_pos > start_arr:
                slice_candidate = raw_str[:curr_pos + 1].strip()
                slice_candidate = re.sub(r",\s*$", "", slice_candidate)
                slice_candidate += "\n  ]\n}"
                slice_candidate = re.sub(r",\s*([\]}])", r"\1", slice_candidate)
                try:
                    res = json.loads(slice_candidate)
                    if isinstance(res.get("scenes"), list) and len(res["scenes"]) >= 1:
                        print(f"[ScriptGenerator] Auto-reparación exitosa: {len(res['scenes'])} escenas recuperadas de respuesta truncada.")
                        return res
                except Exception:
                    pass
                curr_pos = raw_str.rfind("}", 0, curr_pos)

        # 3. Fallback: Extracción por expresiones regulares de escenas individuales
        scene_blocks = re.findall(
            r'\{\s*"scene_id"\s*:\s*(\d+)[^}]*?"text"\s*:\s*"([^"]+)"[^}]*?\}',
            raw_str,
            flags=re.DOTALL
        )
        if len(scene_blocks) >= 1:
            rec_scenes = []
            for sid, txt in scene_blocks:
                rec_scenes.append({
                    "scene_id": int(sid),
                    "text": txt,
                    "image_prompt": f"Cinematic visual representing {txt[:60]}",
                    "keywords": [txt.split()[0]] if txt.split() else ["escena"],
                    "media_type": "ai_image"
                })
            title_m = re.search(r'"title"\s*:\s*"([^"]+)"', raw_str)
            hook_m = re.search(r'"hook"\s*:\s*"([^"]+)"', raw_str)
            return {
                "title": title_m.group(1) if title_m else "Video Documental",
                "niche": "curiosidades",
                "hook": hook_m.group(1) if hook_m else "",
                "scenes": rec_scenes
            }

        return None

    def _parse_llm_json(
        self,
        raw_text: str,
        fallback_topic: str,
        fallback_niche: str,
        target_duration: int = 30,
        scene_count: Optional[int] = None
    ) -> ReelScript:
        """Limpia y parsea la respuesta JSON de un LLM. Lanza ValueError si no es recuperable."""
        cleaned = (raw_text or "").strip()
        # Eliminar etiquetas de razonamiento tipo <think> ... </think>
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()
        first_brace = cleaned.find("{")
        if first_brace != -1:
            cleaned = cleaned[first_brace:].strip()

        data = self._repair_and_load_json(cleaned)
        if not data or not isinstance(data.get("scenes"), list) or not data["scenes"]:
            raise ValueError("La respuesta del LLM no contiene una lista de escenas válida.")

        scenes = []
        for idx, item in enumerate(data["scenes"]):
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue

            def pick(key: str, valid: tuple, default: str) -> str:
                value = item.get(key, default)
                return value if value in valid else default

            accent = str(item.get("accent_color", "&H0000E5FF&"))
            scenes.append(Scene(
                scene_id=len(scenes) + 1,
                text=str(item["text"]).strip(),
                image_prompt=item.get("image_prompt") or f"photorealistic cinematic shot of {fallback_topic}",
                keywords=item.get("keywords") or [],
                media_type=pick("media_type", VALID_MEDIA, "ai_image"),
                pexels_query=item.get("pexels_query", "") or "",
                camera_motion=pick("camera_motion", VALID_CAMERAS, "fast_punch_zoom" if idx == 0 else "slow_dolly_in"),
                transition_out=pick("transition_out", VALID_TRANSITIONS, "smash_cut"),
                visual_vfx=pick("visual_vfx", VALID_VFX, "deep_vignette" if idx == 0 else "cinematic_clean"),
                sfx_cue=pick("sfx_cue", VALID_SFX, "impact_boom" if idx == 0 else "none"),
                subtitle_mode=pick("subtitle_mode", VALID_SUB_MODES, "single_word_punch" if idx == 0 else "dynamic_pop"),
                accent_color=accent if ASS_COLOR_RE.match(accent) else "&H0000E5FF&",
                broll=[str(q).strip() for q in (item.get("broll") or []) if str(q).strip()][:3],
            ))
        if not scenes:
            raise ValueError("Todas las escenas del LLM llegaron vacías.")

        hook = str(data.get("hook", "")).strip()
        # Algunos modelos repiten el hook como texto de la escena 1: la voz lo diría dos veces.
        norm = lambda text: re.sub(r"\W+", "", text.lower())
        if hook and norm(scenes[0].text).startswith(norm(hook)):
            remainder = scenes[0].text.strip()[len(hook):].strip()
            if remainder:
                scenes[0].text = remainder
            else:
                hook = ""
        full_text = f"{hook} " + " ".join(s.text for s in scenes)
        hashtags = data.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = hashtags.split()

        return ReelScript(
            title=data.get("title") or fallback_topic,
            niche=data.get("niche") or fallback_niche,
            hook=hook,
            scenes=scenes,
            cta="",
            full_script_text=full_text.strip(),
            caption=str(data.get("caption", "")).strip(),
            hashtags=_hashtags_from(hashtags),
            on_screen_title=str(data.get("on_screen_title", "")).strip()[:60],
        )


def _style_hint(visual_style: str, max_terms: int = 3) -> str:
    """Toma solo descriptores de estilo (luz, grano, render) del estilo del nicho, no su contenido."""
    style_words = ("light", "lighting", "grain", "film", "cinematic", "contrast", "glow", "photorealistic", "8k", "render", "atmosphere")
    dark_words = ("dark", "dim", "noir", "moody", "shadow", "night", "fog", "eerie", "horror", "chiaroscuro")
    terms = [t.strip() for t in (visual_style or "").split(",")]
    picked = [t for t in terms if any(w in t.lower() for w in style_words) and not any(d in t.lower() for d in dark_words)]
    return ", ".join(picked[:max_terms]) or "well lit, high detail"


def _as_list(value: Any) -> List[str]:
    """Lista de textos aunque el modelo devuelva un texto suelto en lugar de una lista."""
    items = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in items if item and str(item).strip()]


def _hashtags_from(words: List[str], limit: int = 8) -> List[str]:
    tags = []
    for word in words:
        tag = re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]", "", str(word).replace("#", "").title().replace(" ", ""))
        if tag and f"#{tag}" not in tags:
            tags.append(f"#{tag}")
    return tags[:limit]
