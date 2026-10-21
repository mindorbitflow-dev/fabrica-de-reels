"""
Reel Pipeline Orchestrator
Guion -> Voz y tiempos por palabra -> Medios -> Subtítulos ASS -> Mezcla de audio -> Render -> Paquete de publicación.

Cada paso queda registrado en output/<job_id>/manifest.json. Relanzar el mismo
job_id reanuda desde el último paso completado en lugar de empezar de cero.
"""

import os
import re
import sys
import json
import shutil
import subprocess
import hashlib
import datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from shutil import which as shutil_which
from typing import Dict, Any, Optional, Callable, Iterable, List, Set

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.core.env import load_env
from src.core.script_generator import ScriptGenerator, ReelScript, Scene
from src.core.tts_engine import TTSEngine, AudioResult, WordTiming, spoken_tokens, get_media_duration
from src.core.media_engine import MediaEngine, fetch_commons_image, fetch_wikipedia_lead_image
from src.core.motion_engine import MotionEngine
from src.core.subtitles_engine import SubtitlesEngine, pick_pop_words
from src.core.voice_gemini import GeminiVoice
from src.core.audio_engine import AudioEngine
from src.core.video_renderer import VideoRenderer, quantize_durations
from src.core.niche_manager import NicheManager
from src.core.job_manager import JobManifest, write_json_atomic
from src.core.production import ProductionPolicy, plan_shots
from src.core.researcher import Dossier, Researcher
from src.core.news import COLOMBIA_PHOTO, FOREIGN_PHOTO, NEWS_NICHE, NewsDesk
from src.core.topic_picker import TopicPicker, is_random_topic
from src.core.post_image import POST_SIZE, render_post

ASPECT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


@dataclass
class GenerationResult:
    video_path: str
    script: ReelScript
    audio_path: str
    subtitles_path: Optional[str]
    image_paths: List[str]
    duration: float
    title: str
    job_id: str = ""
    status: str = "completed"
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    manifest_path: str = ""
    publication_package: str = ""
    language: str = "es"


def make_job_id(topic: str, now: Optional[datetime.datetime] = None, prefix: str = "reel") -> str:
    topic_norm = unicodedata.normalize("NFKD", topic or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9_\- ]", "", topic_norm).strip().replace(" ", "_")[:25] or "video"
    return f"{prefix}_{(now or datetime.datetime.now()).strftime('%Y%m%d_%H%M%S')}_{slug}"


def calculate_scene_durations(script: ReelScript, total_duration: float, words: Optional[List[WordTiming]] = None) -> List[float]:
    """Duración de cada escena. Con tiempos por palabra, cada corte cae en la mitad del
    silencio entre la última palabra de una escena y la primera de la siguiente."""
    scenes = script.scenes
    if not scenes:
        return [total_duration]
    counts = [len(spoken_tokens(s.text)) for s in scenes]
    counts[0] += len(spoken_tokens(script.hook))

    aligned = [w for w in (words or []) if w.token_index >= 0]
    if len(aligned) >= len(scenes):
        cuts, start_token = [], 0
        for count in counts[:-1]:
            start_token += count
            j = next((k for k, w in enumerate(aligned) if w.token_index >= start_token), None)
            if j is None or j == 0:
                break
            cuts.append((aligned[j - 1].end + aligned[j].start) / 2)
        if len(cuts) == len(scenes) - 1 and all(b > a for a, b in zip([0.0] + cuts, cuts + [total_duration])):
            bounds = [0.0] + cuts + [total_duration]
            return [b - a for a, b in zip(bounds, bounds[1:])]

    # Sin alineación fiable: reparto proporcional a las palabras.
    total_words = sum(max(1, c) for c in counts)
    return [max(1, c) / total_words * total_duration for c in counts]


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def sentence_cuts(script: ReelScript, words: Optional[List[WordTiming]] = None) -> List[List[Optional[float]]]:
    """Por escena, el instante (mitad del silencio) entre cada frase y la siguiente; None si
    esa frontera no se pudo alinear. El hook se suma a la primera frase de la escena 1."""
    by_token = {w.token_index: w for w in (words or []) if w.token_index >= 0}
    token = len(spoken_tokens(script.hook))
    result = []
    for scene in script.scenes:
        sentences = [s for s in _SENTENCE_END.split(scene.text.strip()) if spoken_tokens(s)] or [scene.text]
        cuts: List[Optional[float]] = []
        for sentence in sentences[:-1]:
            token += len(spoken_tokens(sentence))
            before, after = by_token.get(token - 1), by_token.get(token)
            cuts.append((before.end + after.start) / 2 if before and after else None)
        token += len(spoken_tokens(sentences[-1]))
        result.append(cuts)
    return result


class ReelFactoryPipeline:
    def __init__(self, config_path: str = "config/config.yaml"):
        load_env()
        self.config = self._load_config(config_path)
        self.policy = ProductionPolicy.from_config(self.config)
        self.niche_manager = NicheManager()
        prod_cfg = self.config.get("production", {})
        video_cfg = self.config.get("video", {})
        self.fps = video_cfg.get("fps", 30)

        self.script_gen = ScriptGenerator(preferred_provider=self.config.get("llm", {}).get("preferred_provider", "auto"))
        self.tts = TTSEngine(
            default_voice=self.config.get("voice", {}).get("default_voice", "es-MX-JorgeNeural"),
            rate=self.config.get("voice", {}).get("rate", "+5%")
        )
        self.media = MediaEngine(output_dir="output/temp_media", image_concurrency=self.policy.network_concurrency)
        self.motion = MotionEngine(fps=self.fps)

        sub_cfg = self.config.get("subtitles", {})
        self.subtitles = SubtitlesEngine(
            font_name=sub_cfg.get("font_name", "Arial"),
            font_size=sub_cfg.get("font_size", 72),
            margin_v=sub_cfg.get("margin_v", 400),
            words_per_line=sub_cfg.get("words_per_line", 3),
            uppercase=sub_cfg.get("uppercase", True)
        )

        self.audio = AudioEngine(music_dir="assets/music")
        self.renderer = VideoRenderer(
            width=video_cfg.get("width", 1080),
            height=video_cfg.get("height", 1920),
            fps=self.fps,
            render_profile=self.policy.render_profile,
            workers=prod_cfg.get("render_workers", 2),
            encoder=video_cfg.get("encoder", "auto"),
            progress_bar=self.config.get("overlays", {}).get("progress_bar", True),
        )
        self.keep_intermediates = bool(prod_cfg.get("keep_intermediates", False))
        self.researcher = Researcher(self.script_gen.llm)
        self.gemini_voice = GeminiVoice()

    def _load_config(self, path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def preflight(self) -> Dict[str, Any]:
        """Diagnóstico no sensible que la UI y el lote pueden ejecutar antes de producir."""
        ffmpeg_ok = shutil_which("ffmpeg") is not None and shutil_which("ffprobe") is not None
        return {
            "ready": ffmpeg_ok,
            "ffmpeg": ffmpeg_ok,
            "llm_providers": self.script_gen.llm.available_providers(),
            "pexels": bool(self.media.pexels_key),
            "assets_music": os.path.isdir("assets/music"),
            "assets_sfx": os.path.isdir("assets/sfx"),
            "output_writable": os.access("output", os.W_OK) if os.path.exists("output") else os.access(".", os.W_OK),
            "niche_count": len(self.niche_manager.get_all_niches()),
            "policy": asdict(self.policy),
        }

    @staticmethod
    def _script_from_dict(data: Dict[str, Any]) -> ReelScript:
        return ReelScript.from_dict(data)

    def platform_profile(self, platform: Optional[str]) -> Dict[str, Any]:
        return (self.config.get("platforms", {}) or {}).get(platform or "", {}) or {}

    def voice_engine_name(self, requested: Optional[str] = None) -> str:
        """Motor de voz efectivo: Gemini solo si hay clave; así el presupuesto de palabras coincide."""
        engine = (requested or self.config.get("voice", {}).get("engine", "edge")).lower()
        return "gemini" if engine == "gemini" and self.gemini_voice.available else "edge"

    def research_by_default(self, niche: str) -> bool:
        return niche in (self.config.get("research", {}) or {}).get("default_niches", [])

    def exposure_target_for(self, niche: str) -> int:
        """Los nichos de tono sombrío conservan su ambiente con una exposición objetivo más baja."""
        tone = " ".join(str(v) for v in self.niche_manager.get_niche(niche).values() if isinstance(v, str)).lower()
        dark = any(word in tone for word in ("oscur", "sombr", "misterio", "terror", "suspenso", "noir", "eerie"))
        return self.media.EXPOSURE_TARGET_DARK if dark else self.media.EXPOSURE_TARGET

    def generate_reel(
        self,
        topic: str,
        niche: str = "curiosidades",
        voice: Optional[str] = None,
        voice_rate: Optional[str] = None,
        target_duration: int = 30,
        scene_count: Optional[int] = None,
        aspect_ratio: str = "9:16",
        enable_subtitles: bool = True,
        subtitle_style: str = "yellow_pop",
        subtitle_position: str = "bottom",
        subtitle_font_size: Optional[int] = None,
        words_per_line: Optional[int] = None,
        subtitle_uppercase: Optional[bool] = None,
        music_track: Optional[str] = None,
        music_volume: Optional[float] = None,
        zoom_intensity: float = 1.0,
        custom_script: Optional[ReelScript] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        production_profile: Optional[str] = None,
        job_id: Optional[str] = None,
        resume: bool = True,
        platform: Optional[str] = None,
        research: Optional[bool] = None,
        language: str = "es",
        shared_media_dir: Optional[str] = None,
        cleanup: bool = True,
        voice_engine: Optional[str] = None,
        background_music: Optional[bool] = None,
    ) -> GenerationResult:
        """Ejecuta el pipeline completo. Con un job_id existente reanuda donde quedó.

        platform: "tiktok" (>60 s, apto para el Programa de Creadores) o "reels".
        research: investigar el tema (Wikipedia + búsqueda) y escribir un documental basado en hechos.
        shared_media_dir: reutiliza los medios de otro trabajo (versión en otro idioma del mismo guion).
        """

        def update_progress(percent: int, message: str):
            if progress_callback:
                progress_callback(percent, message)
            print(f"[{percent}%] {message}")

        if not self.preflight()["ffmpeg"]:
            raise RuntimeError("FFmpeg y FFprobe son obligatorios para producir un reel.")

        policy = ProductionPolicy.from_config(self.config, production_profile)
        width, height = ASPECT_SIZES.get(aspect_ratio, ASPECT_SIZES["9:16"])
        profile = self.platform_profile(platform)
        if profile and not custom_script:
            low, high = profile.get("min_duration", 10), profile.get("max_duration", 600)
            if not low <= target_duration <= high:
                target_duration = profile.get("default_duration", low)
        research = self.research_by_default(niche) if research is None else research

        # Tema aleatorio: se resuelve antes del job_id para que título, carpeta y publicación
        # lleven el tema real. Al reanudar un trabajo existente se conserva el tema ya elegido.
        picked_topic = False
        existing_script = job_id and os.path.exists(os.path.join("output", job_id, "script.json"))
        if is_random_topic(topic) and not custom_script and not existing_script:
            update_progress(5, f"Eligiendo un tema aleatorio del nicho '{niche}'...")
            topic = self.script_gen.topics.pick(niche, remember=False)
            picked_topic = True
            print(f"[Pipeline] Tema aleatorio: {topic}")
        job_id = job_id or make_job_id(topic)
        if picked_topic:
            self.script_gen.topics.remember(niche, topic, job_id)
        work_dir = os.path.join("output", job_id)
        media_dir = shared_media_dir or os.path.join(work_dir, "media")
        os.makedirs(media_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)

        manifest = JobManifest(work_dir, job_id, {
            "topic": topic, "niche": niche, "target_duration": target_duration,
            "scene_count": scene_count, "aspect_ratio": aspect_ratio, "voice": voice,
            "platform": platform, "research": research, "language": language,
            "policy": asdict(policy),
        })
        manifest.reset_for_rerun()
        manifest.set_status("running")
        render_warnings: List[str] = []

        def step(name: str, func: Callable):
            manifest.begin_step(name)
            try:
                value = func()
            except Exception as error:
                manifest.fail(name, error)
                raise RuntimeError(f"[{job_id}] Falló el paso '{name}': {error}") from error
            manifest.complete_step(name)
            return value

        # 1. Investigación (documentales) -------------------------------------------
        dossier: Optional[Dossier] = None
        if research and not custom_script:
            update_progress(8, f"Investigando '{topic}' (Wikipedia y búsqueda web)...")
            research_path = os.path.join(work_dir, "research.json")

            def make_research() -> Dossier:
                if resume and os.path.exists(research_path):
                    with open(research_path, "r", encoding="utf-8") as file:
                        return Dossier.from_dict(json.load(file))
                result = self.researcher.research(topic, language)
                write_json_atomic(research_path, result.to_dict())
                return result

            dossier = step("research", make_research)
            if not dossier.facts:
                manifest.warn("research", "No se encontraron fuentes para el tema; el guion se escribió sin dossier.")
                dossier = None
            elif dossier.method != "wikipedia+busqueda":
                manifest.warn("research", "Investigación solo con Wikipedia (la búsqueda web necesita GEMINI_API_KEY con cuota).")

        # 2. Guion ------------------------------------------------------------
        update_progress(12, f"Generando guion (duración objetivo: {target_duration}s)...")
        script_path = os.path.join(work_dir, "script.json")

        def make_script() -> ReelScript:
            if custom_script:
                script = custom_script
            elif resume and os.path.exists(script_path):
                with open(script_path, "r", encoding="utf-8") as file:
                    script = ReelScript.from_dict(json.load(file))
            else:
                script = self.script_gen.generate(
                    topic=topic, niche=niche, target_duration=target_duration, scene_count=scene_count,
                    platform=platform, research_brief=dossier.brief() if dossier else None,
                    voice_engine=self.voice_engine_name(voice_engine),
                )
                if dossier and script.source == "offline":
                    script = self.script_gen.script_from_facts(topic, niche, [f.text for f in dossier.facts],
                                                               target_duration, scene_count)
                    script.sources = dossier.source_list()
                elif dossier:
                    texts, corrections = self.researcher.verify_script(script.scenes, dossier)
                    for scene, text in zip(script.scenes, texts):
                        scene.text = text
                    manifest.data.setdefault("artifacts", {})["fact_check"] = {
                        "facts": len(dossier.facts), "corrected_scenes": corrections,
                    }
                    script.sources = dossier.source_list()
            if not script.scenes:
                raise ValueError("El guion no tiene escenas.")
            script.language = language
            # El texto locutado siempre se reconstruye para que la voz y las escenas coincidan.
            script.full_script_text = " ".join(t for t in [script.hook.strip()] + [s.text.strip() for s in script.scenes] if t)
            write_json_atomic(script_path, script.to_dict())
            return script

        script = step("script", make_script)
        if script.source == "offline" and not custom_script:
            manifest.warn("script", "Guion genérico (modo offline) porque la IA no respondió. " + self.script_gen.llm.failure_summary())
        elif script.source == "dossier":
            manifest.warn("script", "Sin guionista IA: el documental narra los hechos de Wikipedia tal cual (añade GEMINI_API_KEY para un guion redactado).")

        # 3. Voz ----------------------------------------------------------------
        update_progress(25, "Sintetizando voz y tiempos por palabra...")
        words_path = os.path.join(work_dir, "words.json")
        voice_cfg = self.config.get("voice", {})
        engine = (voice_engine or voice_cfg.get("engine", "edge")).lower()
        default_voice = voice_cfg.get("default_voice_en", "en-US-ChristopherNeural") if language == "en" else voice_cfg.get("default_voice", "es-MX-JorgeNeural")
        selected_voice = voice if voice and voice.lower().startswith(language) else default_voice
        selected_rate = voice_rate or voice_cfg.get("rate", "+5%")
        gemini_voices = voice_cfg.get("gemini_voices", {}) or {}
        gemini_voice = gemini_voices.get(niche) or gemini_voices.get("default", "Charon")
        voice_label = f"gemini:{gemini_voice}" if engine == "gemini" else f"edge:{selected_voice}|{selected_rate}"
        voice_key = hashlib.sha1(f"{voice_label}|{script.full_script_text}".encode("utf-8")).hexdigest()

        def edge_voice() -> AudioResult:
            path = os.path.join(work_dir, "voice.mp3")
            return self.tts.synthesize(text=script.full_script_text, output_path=path, voice=selected_voice, rate=selected_rate)

        def gemini_voice_track() -> AudioResult:
            """Voz Gemini + control de calidad con Whisper: la transcripción debe cubrir el guion."""
            from src.core.aligner import align_audio
            raw = os.path.join(work_dir, "voice_gemini_raw.wav")
            path = os.path.join(work_dir, "voice.wav")
            self.gemini_voice.synthesize(script.full_script_text, raw, gemini_voice)
            alignment = align_audio(raw, script.full_script_text, language)
            if alignment.coverage < 0.85:
                raise RuntimeError(f"la voz no coincide con el guion (cubre {alignment.coverage:.0%})")
            trim = max(0.0, alignment.extra_leading_seconds - 0.12) if alignment.extra_leading_seconds > 0.4 else 0.0
            if trim:
                manifest.warn("voice", f"La voz añadió {trim:.1f}s de texto que no estaba en el guion; se recortó.")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{trim:.3f}", "-i", raw, "-c:a", "pcm_s16le", path], check=True)
            os.remove(raw)
            words = [WordTiming(w.word, max(0.0, w.start - trim), max(0.05, w.end - trim), w.token_index) for w in alignment.words]
            return AudioResult(path, get_media_duration(path), words)

        def make_voice() -> AudioResult:
            cached_file = manifest.artifact("voice_file")
            if resume and manifest.artifact("voice_key") == voice_key and cached_file and os.path.exists(cached_file) and os.path.exists(words_path):
                return AudioResult.load(cached_file, words_path)
            result = None
            if engine == "gemini":
                if not self.gemini_voice.available:
                    manifest.warn("voice", "La voz Gemini necesita GEMINI_API_KEY; se usó la voz de Edge.")
                else:
                    try:
                        result = gemini_voice_track()
                    except Exception as error:
                        manifest.warn("voice", f"Voz Gemini no disponible ({str(error)[:140]}); se usó la voz de Edge.")
            if result is None:
                result = edge_voice()
            result.save_words(words_path)
            manifest.data.setdefault("artifacts", {}).update({"voice_key": voice_key, "voice_file": result.audio_path})
            return result

        audio_res = step("voice", make_voice)
        voice_path = audio_res.audio_path
        total_duration = audio_res.duration
        scene_durations = quantize_durations(calculate_scene_durations(script, total_duration, audio_res.words), self.fps)
        total_duration = sum(scene_durations)
        print(f"[Pipeline] Escenas: {[round(d, 2) for d in scene_durations]}s (total {total_duration:.2f}s)")
        if abs(total_duration - target_duration) > max(3.0, target_duration * 0.25) and not custom_script:
            manifest.warn("voice", f"La locución dura {total_duration:.1f}s frente a {target_duration}s pedidos.")
        if profile.get("min_duration") and total_duration < profile["min_duration"] - 4:
            manifest.warn("voice", f"Dura {total_duration:.0f}s: por debajo del mínimo de {platform} ({profile['min_duration']}s).")

        # 4. Medios ---------------------------------------------------------------
        update_progress(40, f"Consiguiendo visuales ({aspect_ratio})...")
        shot_seconds = policy.target_visual_seconds(total_duration)
        exposure = self.exposure_target_for(niche)
        is_anime = niche == "anime_manga"
        used_stock: set = set()
        used_commons: set = set()

        def fetch_scene_media(i: int):
            scene = script.scenes[i]
            notes, credits, paths = [], [], []
            wanted = max(1, min(4, round(scene_durations[i] / max(shot_seconds, 0.5))))
            query = (scene.pexels_query or "").strip()
            if research and query and not is_anime:
                for k in range(wanted):
                    archive = fetch_commons_image(self.media, query, os.path.join(media_dir, f"commons_{i + 1:02d}_{k + 1}.jpg"),
                                                  width, height, exposure_target=exposure, exclude=used_commons)
                    if not archive:
                        break
                    paths.append(archive.path)
                    credits.append(archive.note)
            if scene.media_type == "pexels_video" and query and not is_anime:
                # Una búsqueda distinta por frase (broll del director), en el orden en que se dicen;
                # si faltan, se repite la principal. Un clip que no aparece ocupa el de su vecino
                # para que cada frase siga mostrando su propia búsqueda.
                broll = list(getattr(scene, "broll", None) or [])
                wanted = max(wanted, len(broll))
                queries = (broll + [query] * wanted)[:wanted]
                clips: List[Optional[str]] = list(paths)
                for k, stock_query in enumerate(queries[len(paths):], start=len(paths)):
                    clip_path = os.path.join(media_dir, f"stock_{i + 1:02d}_{k + 1}.mp4")
                    clip = self.media.fetch_video(stock_query, clip_path, width, height,
                                                  min_duration=scene_durations[i] / wanted, exclude_ids=used_stock)
                    clip = clip or self.media.fetch_pixabay_video(stock_query, clip_path, width, height, exclude_ids=used_stock)
                    clips.append(clip.path if clip else None)
                    if not clip:
                        notes.append(f"Escena {i + 1}: sin clip para '{stock_query}', se repite el plano vecino.")
                first = next((c for c in clips if c), None)
                paths, previous = [], first
                for clip_file in clips:
                    previous = clip_file or previous
                    paths.append(previous)
                paths = paths if first else []
                if not paths:
                    notes.append(f"Escena {i + 1}: sin clip de stock para '{query}', se usó imagen IA.")
            if not paths:
                image = self.media.fetch_image(
                    scene.image_prompt, os.path.join(media_dir, f"scene_{i + 1:02d}.jpg"), width, height,
                    retries=policy.provider_retries, exposure_target=exposure,
                    upscale_model="realesr-animevideov3",
                )
                if image.is_fallback and query and not is_anime:
                    photo = self.media.fetch_photo(query, os.path.join(media_dir, f"photo_{i + 1:02d}.jpg"),
                                                   width, height, exposure_target=exposure)
                    if photo:
                        notes.append(f"Escena {i + 1}: Pollinations falló; se usó una foto de Pexels.")
                        image = photo
                if image.is_fallback:
                    notes.append(f"Escena {i + 1}: imagen de respaldo ({image.note}).")
                paths.append(image.path)
            return paths, notes, credits

        def make_media() -> List[List[str]]:
            with ThreadPoolExecutor(max_workers=max(1, policy.network_concurrency)) as pool:
                results = list(pool.map(fetch_scene_media, range(len(script.scenes))))
            credits = []
            for _, notes, scene_credits in results:
                for note in notes:
                    manifest.warn("media", note)
                credits.extend(scene_credits)
            manifest.data.setdefault("artifacts", {})["credits"] = credits
            return [paths for paths, _, _ in results]

        scene_media = step("media", make_media)
        pauses = [(a.end + b.start) / 2 for a, b in zip(audio_res.words, audio_res.words[1:]) if b.start - a.end >= 0.12]
        shots = plan_shots(script.scenes, scene_durations, scene_media, shot_seconds, self.fps, cut_points=pauses,
                           sentence_cuts=sentence_cuts(script, audio_res.words))
        print(f"[Pipeline] {len(shots)} planos para {len(script.scenes)} escenas (~{shot_seconds}s por plano).")

        # 5. Subtítulos -------------------------------------------------------------
        subtitles_path = None
        if enable_subtitles and subtitle_style not in ("none", "disabled", "off", None):
            update_progress(60, "Generando subtítulos dinámicos...")
            subtitles_path = os.path.join(work_dir, "subtitles.ass")
            step("subtitles", lambda: self.subtitles.generate_directed_ass(
                words=audio_res.words, output_ass_path=subtitles_path, scenes=script.scenes,
                scene_durations=scene_durations, width=width, height=height,
                font_size=subtitle_font_size, position=subtitle_position,
                style_preset=subtitle_style, words_per_line=words_per_line, uppercase=subtitle_uppercase,
                title_card=(script.on_screen_title or None) if self.config.get("overlays", {}).get("title_card", True) else None,
                pop_words=pick_pop_words(script.scenes, audio_res.words)
                if self.config.get("overlays", {}).get("keyword_pops", True) and script.source not in ("offline", "dossier") else None,
            ))
        else:
            update_progress(60, "Subtítulos desactivados.")

        # 6. Audio --------------------------------------------------------------------
        update_progress(70, "Masterizando voz, SFX y música con ducking...")
        mixed_audio_path = os.path.join(work_dir, "audio_final.wav")
        music_vol = music_volume if music_volume is not None else self.config.get("audio", {}).get("music_volume", 0.12)
        audio_cfg = self.config.get("audio", {})
        use_music = bool(music_track) or (audio_cfg.get("background_music", False) if background_music is None else background_music)
        chosen_music = (music_track or manifest.artifact("music_track") or self.audio.get_music_track()) if use_music else None
        manifest.data.setdefault("artifacts", {})["music_track"] = chosen_music

        final_audio = step("audio", lambda: self.audio.mix_directed_audio(
            voice_path=voice_path, output_mixed_path=mixed_audio_path, total_duration=total_duration,
            scene_durations=scene_durations, sfx_cues=[s.sfx_cue for s in script.scenes],
            music_path=chosen_music, music_volume=music_vol,
            use_music=use_music, sfx_mode=audio_cfg.get("sfx", "minimal"),
        ))

        # 7. Render -------------------------------------------------------------------
        update_progress(80, f"Renderizando {len(shots)} planos en {width}x{height}...")
        final_video_path = os.path.join("output", f"{job_id}.mp4")
        step("render", lambda: self.renderer.render_directed_reel(
            scene_images=[s.media_path for s in shots], scene_durations=[s.duration for s in shots], scenes=shots,
            audio_path=final_audio, subtitles_path=subtitles_path, output_video_path=final_video_path,
            total_duration=total_duration, motion_engine=self.motion, width=width, height=height,
            work_dir=work_dir, zoom_intensity=zoom_intensity, warnings=render_warnings,
            exposure_target=exposure,
        ))
        for note in render_warnings:
            manifest.warn("render", note)

        # 8. Verificación + paquete de publicación -----------------------------------------
        update_progress(95, "Verificando el MP4 y preparando la publicación...")

        def verify_and_package() -> str:
            real = get_media_duration(final_video_path)
            if abs(real - total_duration) > 0.5:
                manifest.warn("verify", f"El MP4 dura {real:.2f}s y el audio {total_duration:.2f}s.")
            caption = script.caption or script.hook
            credits = manifest.artifact("credits") or []
            extras = []
            if script.sources:
                extras.append(("Fuentes:" if language == "es" else "Sources:") + "\n" + "\n".join(f"• {s}" for s in script.sources))
            if credits:
                extras.append(("Imágenes:" if language == "es" else "Images:") + "\n" + "\n".join(f"• {c}" for c in credits))
            package_path = os.path.join(work_dir, "publicacion.json")
            write_json_atomic(package_path, {
                "video": os.path.abspath(final_video_path),
                "title": script.title,
                "caption": caption,
                "hashtags": script.hashtags,
                "caption_completo": f"{caption}\n\n{' '.join(script.hashtags)}".strip(),
                "fuentes_y_creditos": "\n\n".join(extras),
                "duration_seconds": round(real, 2),
                "aspect_ratio": aspect_ratio,
                "platform": platform,
                "language": language,
                "niche": niche,
                "script_source": script.source,
            })
            return package_path

        package_path = step("package", verify_and_package)
        status = manifest.finish()

        if cleanup and not shared_media_dir and not self.keep_intermediates and status == "completed":
            shutil.rmtree(media_dir, ignore_errors=True)

        update_progress(100, "¡Reel completado!" if status == "completed" else "Reel completado con avisos (revisa manifest.json).")
        return GenerationResult(
            video_path=final_video_path,
            script=script,
            audio_path=final_audio,
            subtitles_path=subtitles_path,
            image_paths=[s.media_path for s in shots],
            duration=total_duration,
            title=script.title,
            job_id=job_id,
            status=status,
            warnings=manifest.data.get("warnings", []),
            manifest_path=manifest.path,
            publication_package=package_path,
            language=language,
        )

    def create_news_post(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> str:
        """Post de la página de noticias: la noticia colombiana más reciente sin publicar, contada solo con su artículo."""
        topics = TopicPicker(history_path="output/news_history.json")
        story, dossier = NewsDesk().pick(topics.used_topics(NEWS_NICHE))
        print(f"📰 {story.outlet}: {story.title}")
        package_path = self.create_post(story.title, NEWS_NICHE, dossier=dossier, progress_callback=progress_callback,
                                        used_photos=topics.used_photos(NEWS_NICHE))
        with open(package_path, "r", encoding="utf-8") as file:
            photo = json.load(file).get("image_id", "")
        topics.remember(NEWS_NICHE, story.title, os.path.basename(os.path.dirname(package_path)), photo=photo)
        return package_path

    def create_post(self, topic: str, niche: str = "curiosidades", research: Optional[bool] = None,
                    progress_callback: Optional[Callable[[int, str], None]] = None,
                    dossier: Optional[Dossier] = None, used_photos: Iterable[str] = ()) -> str:
        """Post de imagen: tema → texto (con investigación si toca, o con el dossier dado) → imagen → titular encima.
        Devuelve la ruta de su publicacion.json, que el publicador entiende igual que la de un reel."""
        def update_progress(percent: int, message: str):
            if progress_callback:
                progress_callback(percent, message)
            print(f"[{percent}%] {message}")

        picked = is_random_topic(topic)
        if picked:
            topic = self.script_gen.topics.pick(niche, remember=False)
        job_id = make_job_id(topic, prefix="post")
        if picked:
            self.script_gen.topics.remember(niche, topic, job_id)
        work_dir = os.path.join("output", job_id)
        os.makedirs(work_dir, exist_ok=True)

        if dossier is None and (self.research_by_default(niche) if research is None else research):
            update_progress(10, f"Investigando '{topic}'...")
            dossier = self.researcher.research(topic)
            dossier = dossier if dossier.facts else None

        update_progress(30, "Escribiendo el post...")
        # Las noticias escriben con Groq primero: la cuota gratuita de Gemini queda para los reels y posts de la página principal.
        post = self.script_gen.generate_post(topic, niche, dossier.brief() if dossier else None,
                                             preferred="groq" if niche == NEWS_NICHE else None, context=niche == NEWS_NICHE)

        update_progress(50, "Buscando una foto real del tema...")
        image = self._post_image(post, dossier, niche, work_dir, set(used_photos))

        update_progress(85, "Componiendo el titular sobre la imagen...")
        post_cfg = self.config.get("posts", {}) or {}
        styles = post_cfg.get("styles", {}) or {}
        style = styles.get(niche) or styles.get("default", "editorial")
        kicker = post["kicker"] or niche.replace("_", " ").upper()
        handle = (post_cfg.get("handles", {}) or {}).get(niche, post_cfg.get("handle", ""))
        post_path = render_post(image.path, post["headline"], os.path.join(work_dir, "post.jpg"), style=style,
                                highlight=post["highlight"], kicker=kicker, handle=handle, context=post["context"])
        sources = dossier.source_list() if dossier else []
        if image.note and "Commons" in image.note:
            sources.append(f"Imagen: {image.note}")
        if niche == NEWS_NICHE:
            sources.append("Imagen ilustrativa: Pexels")
        package_path = os.path.join(work_dir, "publicacion.json")
        write_json_atomic(package_path, {
            "type": "post",
            "image": os.path.abspath(post_path),
            "image_id": image.source_id,
            "title": post["title"],
            "headline": post["headline"],
            "caption": post["caption"],
            "hashtags": post["hashtags"],
            "caption_completo": f"{post['caption']}\n\n{' '.join(post['hashtags'])}".strip(),
            "fuentes_y_creditos": ("Fuentes:\n" + "\n".join(f"• {s}" for s in sources)) if sources else "",
            "niche": niche,
            "language": "es",
            "script_source": post["source"],
            "image_fallback": image.is_fallback,
        })
        update_progress(100, "¡Post listo!" if not image.is_fallback else "Post listo, pero con imagen de respaldo.")
        return package_path

    def _post_image(self, post: Dict[str, Any], dossier: Optional[Dossier], niche: str, work_dir: str,
                    used_photos: Set[str] = frozenset()):
        """De lo más real a lo menos: foto principal del artículo de Wikipedia sobre el tema,
        foto de Pexels y, solo si no hay ninguna, imagen IA de Pollinations. (Una búsqueda libre en
        Commons se descartó: devolvía fotos del sitio equivocado.)"""
        width, height = POST_SIZE
        exposure = self.exposure_target_for(niche)
        if niche == NEWS_NICHE:
            # Noticia real: nunca una imagen IA que pueda pasar por foto del suceso, solo foto de archivo ilustrativa.
            # De más a menos contexto: la escena del tema en Colombia, la escena del tema sin situarla en otro país
            # (sin filtrar, "congress Colombia" devolvía el Capitolio de Estados Unidos) y, si no, una ciudad colombiana.
            # Nunca una foto ya publicada: antes dos noticias seguidas caían en la misma vista de Bogotá.
            scene = " ".join(post["pexels_query"].split()[:2])   # el buscador solo toma 3 palabras: deja sitio a "Colombia"
            searches = [(f"{scene} Colombia", COLOMBIA_PHOTO, None), (post["pexels_query"], None, FOREIGN_PHOTO)] if scene else []
            searches += [(city, COLOMBIA_PHOTO, None) for city in ("Bogotá Colombia", "Medellín Colombia", "Colombia city", "Cartagena Colombia")]
            for query, must, must_not in searches:
                photo = self.media.fetch_photo(query, os.path.join(work_dir, "foto.jpg"), width, height, exposure_target=exposure,
                                               alt_must_match=must, alt_must_not_match=must_not, exclude_ids=used_photos)
                if photo:
                    return photo
            raise RuntimeError("Pexels no devolvió ninguna foto para la noticia.")
        if dossier and dossier.main_url:
            lead = fetch_wikipedia_lead_image(self.media, dossier.main_url, os.path.join(work_dir, "archivo.jpg"),
                                              width, height, exposure)
            if lead:
                return lead
        if post["pexels_query"]:
            photo = self.media.fetch_photo(post["pexels_query"], os.path.join(work_dir, "foto.jpg"), width, height,
                                           exposure_target=exposure)
            if photo:
                return photo
        return self.media.fetch_image(post["image_prompt"], os.path.join(work_dir, "imagen.jpg"), width, height,
                                      retries=self.policy.provider_retries, exposure_target=exposure,
                                      upscale_model="realesr-animevideov3")

    def draft_script(self, topic: str, niche: str, target_duration: int = 30, scene_count: Optional[int] = None,
                     platform: Optional[str] = None, research: Optional[bool] = None) -> ReelScript:
        """Guion editable para la UI: resuelve el tema aleatorio, investiga si toca, escribe y verifica."""
        if is_random_topic(topic):
            topic = self.script_gen.topics.pick(niche)
        research = self.research_by_default(niche) if research is None else research
        dossier = self.researcher.research(topic) if research else None
        if dossier is not None and not dossier.facts:
            dossier = None
        script = self.script_gen.generate(topic=topic, niche=niche, target_duration=target_duration,
                                          scene_count=scene_count, platform=platform,
                                          research_brief=dossier.brief() if dossier else None,
                                          voice_engine=self.voice_engine_name())
        if dossier and script.source == "offline":
            script = self.script_gen.script_from_facts(topic, niche, [f.text for f in dossier.facts], target_duration, scene_count)
            script.sources = dossier.source_list()
        elif dossier:
            texts, _ = self.researcher.verify_script(script.scenes, dossier)
            for scene, text in zip(script.scenes, texts):
                scene.text = text
            script.full_script_text = " ".join(t for t in [script.hook] + [sc.text for sc in script.scenes] if t)
            script.sources = dossier.source_list()
        return script

    def generate_multilang(self, languages: List[str], progress_callback: Optional[Callable[[int, str], None]] = None,
                           **kwargs) -> List[GenerationResult]:
        """Produce el video en el primer idioma y lo adapta a los demás reutilizando los mismos medios."""
        languages = [lang for lang in dict.fromkeys(languages or ["es"]) if lang]
        primary = self.generate_reel(language=languages[0], cleanup=False, progress_callback=progress_callback, **kwargs)
        results = [primary]
        media_dir = os.path.join("output", primary.job_id, "media")
        for lang in languages[1:]:
            if progress_callback:
                progress_callback(0, f"Adaptando el guion a '{lang}'...")
            try:
                adapted = self.script_gen.adapt(primary.script, lang, platform=kwargs.get("platform"))
            except Exception as error:
                note = f"No se pudo crear la versión '{lang}' (la adaptación necesita un guionista IA): {str(error)[:160]}"
                print(f"[Pipeline] {note}")
                primary.warnings.append({"step": "idioma", "message": note})
                continue
            extra = {k: v for k, v in kwargs.items() if k not in ("topic", "custom_script", "job_id", "voice")}
            results.append(self.generate_reel(
                topic=adapted.title, custom_script=adapted, job_id=f"{primary.job_id}_{lang}",
                language=lang, shared_media_dir=media_dir, cleanup=False,
                progress_callback=progress_callback, **extra,
            ))
        if not self.keep_intermediates and all(r.status == "completed" for r in results):
            shutil.rmtree(media_dir, ignore_errors=True)
        return results

    def _calculate_scene_durations(self, script: ReelScript, total_duration: float, words: Optional[List[Any]] = None) -> List[float]:
        return calculate_scene_durations(script, total_duration, words)
