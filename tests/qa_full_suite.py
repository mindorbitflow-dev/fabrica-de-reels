"""
SUITE DE CONTROL DE CALIDAD Y QA TESTING INTEGRAL
AI Reel Factory - Production Readiness & Stress Testing
"""

import os
import sys
import time
import json
import shutil
import tempfile
import unittest

# Asegurar path raíz del proyecto
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.niche_manager import NicheManager
from src.core.script_generator import ScriptGenerator, ReelScript, Scene
from src.core.tts_engine import TTSEngine
from src.core.media_engine import MediaEngine
from src.core.motion_engine import MotionEngine
from src.core.subtitles_engine import SubtitlesEngine
from src.core.audio_engine import AudioEngine
from src.core.video_renderer import VideoRenderer
from src.core.ai_editor import AIEditorCopilot
from src.pipeline import ReelFactoryPipeline


class TestSuiteQAFull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print("\n========================================================")
        print("  INICIANDO BATERIA DE PRUEBAS QA TESTER EN PROFUNDIDAD")
        print("========================================================")
        cls.temp_dir = tempfile.mkdtemp(prefix="qa_test_")
        cls.niche_mgr = NicheManager()
        cls.script_gen = ScriptGenerator()
        cls.tts = TTSEngine()
        cls.media = MediaEngine()
        cls.motion = MotionEngine()
        cls.subtitles = SubtitlesEngine()
        cls.audio = AudioEngine()
        cls.renderer = VideoRenderer()
        cls.copilot = AIEditorCopilot()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        print("\n========================================================")
        print("  BATERIA DE PRUEBAS QA FINALIZADA CON EXITO")
        print("========================================================")

    # -------------------------------------------------------------
    # 1. QA TEST: GESTOR DE NICHOS
    # -------------------------------------------------------------
    def test_01_niche_manager(self):
        print("\n[QA TEST 1] Verificando Gestor de Nichos...")
        niches = self.niche_mgr.list_niches()
        self.assertGreaterEqual(len(niches), 4, "Debe haber al menos 4 nichos base")
        
        # Probar nicho conocido
        curiosidades = self.niche_mgr.get_niche("curiosidades")
        self.assertIn("visual_style", curiosidades)
        self.assertIn("tone", curiosidades)
        
        # Probar fallback a nicho inexistente
        fallback = self.niche_mgr.get_niche("inexistente_xyz_123")
        self.assertIn("Curiosidades", fallback.get("name", ""), "El fallback debe ser curiosidades")
        
        # Probar creación de nicho dinámico
        self.niche_mgr.add_or_update_niche("qa_niche", "QA Test Niche", "Prueba", "Tono QA", "qa visual style", "es-ES-AlvaroNeural")
        created = self.niche_mgr.get_niche("qa_niche")
        self.assertEqual(created["name"], "QA Test Niche")
        self.niche_mgr.delete_niche("qa_niche")
        print("  -> OK: Gestión de nichos y fallbacks 100% operativos.")

    # -------------------------------------------------------------
    # 2. QA TEST: GENERADOR DE GUIONES & DETECCIÓN DE BUCLES
    # -------------------------------------------------------------
    def test_02_script_generator_non_repeating(self):
        print("\n[QA TEST 2] Verificando Generación de Guiones y Cero Repetición...")
        
        # Test A: Guión corto (30s)
        s30 = self.script_gen._generate_offline(topic="La Pirámide Oculta", niche="curiosidades", target_duration=30)
        self.assertIsInstance(s30, ReelScript)
        self.assertGreaterEqual(len(s30.scenes), 3)
        
        # Test B: Guión largo (300s / 5 min y 600s / 10 min) - Verificar que supere las 3 escenas y NO duplique texto
        s300 = self.script_gen._generate_offline(topic="El Secreto del Dinero", niche="finanzas", target_duration=300)
        scene_texts = [sc.text for sc in s300.scenes]
        self.assertEqual(len(scene_texts), len(set(scene_texts)), "ERROR CRITICO: El guión repitió escenas en formato largo!")
        self.assertGreaterEqual(len(s300.scenes), 8, "El formato de 5 min debe superar holgadamente 3 escenas")
        self.assertLessEqual(len(s300.scenes), 30, "Las escenas no deben desbordar el límite de 30")

        # Test B2: Guión de 10 minutos (600s)
        s600 = self.script_gen._generate_offline(topic="Los Misterios de la Física Cuántica", niche="curiosidades", target_duration=600)
        scene_texts_600 = [sc.text for sc in s600.scenes]
        self.assertEqual(len(scene_texts_600), len(set(scene_texts_600)), "ERROR CRITICO: El guión de 10 min repitió escenas!")
        self.assertGreaterEqual(len(s600.scenes), 18, "El formato de 10 min debe generar entre 18 y 30 escenas ricas")
        self.assertLessEqual(len(s600.scenes), 30, "Las escenas no deben desbordar el límite de 30")

        # Test C: Parser de JSON robusto frente a textos introductorios y markdown
        sample_raw = """
        Here is the JSON response for your request:
        ```json
        {
            "title": "Prueba QA Parser",
            "niche": "curiosidades",
            "hook": "Gancho de prueba para el test unitario.",
            "scenes": [
                {
                    "scene_id": 1,
                    "text": "Esta es la primera escena de prueba.",
                    "media_type": "pexels_video",
                    "pexels_query": "ocean deep",
                    "camera_motion": "fast_punch_zoom",
                    "transition_out": "whip_pan_right",
                    "visual_vfx": "deep_vignette",
                    "sfx_cue": "impact_boom",
                    "subtitle_mode": "single_word_punch",
                    "accent_color": "&H0000E5FF&"
                }
            ]
        }
        ```
        Espero que te sirva.
        """
        parsed = self.script_gen._parse_llm_json(sample_raw, "Prueba", "curiosidades")
        self.assertEqual(parsed.title, "Prueba QA Parser")
        self.assertEqual(len(parsed.scenes), 1)
        self.assertEqual(parsed.scenes[0].camera_motion, "fast_punch_zoom")
        self.assertEqual(parsed.scenes[0].sfx_cue, "impact_boom")
        print("  -> OK: Guiones estructurados, parser resiliente y cero repetición verificado.")

    # -------------------------------------------------------------
    # 3. QA TEST: SÍNTESIS VOCAL NEURONAL (EDGE-TTS)
    # -------------------------------------------------------------
    def test_03_tts_engine(self):
        print("\n[QA TEST 3] Verificando Motor de Voz Neuronal y Alineación de Palabras...")
        voices = TTSEngine.list_spanish_voices()
        self.assertGreater(len(voices), 5, "Debe haber múltiples voces en español")
        
        test_audio_path = os.path.join(self.temp_dir, "qa_voice.mp3")
        test_text = "Esta es una prueba de control de calidad para verificar la síntesis vocal."
        
        t0 = time.time()
        res = self.tts.synthesize(text=test_text, output_path=test_audio_path, voice="es-MX-JorgeNeural", rate="+5%")
        elapsed = time.time() - t0
        
        self.assertTrue(os.path.exists(test_audio_path), "El archivo de voz no se generó")
        self.assertGreater(res.duration, 1.0, "La duración calculada es inválida")
        self.assertGreater(len(res.words), 5, "Debe haber timestamps alineados para cada palabra")
        print(f"  -> OK: Audio generado en {elapsed:.2f}s ({res.duration:.1f}s de locución con {len(res.words)} palabras).")

    # -------------------------------------------------------------
    # 4. QA TEST: MOTOR DE MEDIOS & PEXELS / PLACEHOLDERS
    # -------------------------------------------------------------
    def test_04_media_engine(self):
        print("\n[QA TEST 4] Verificando Motor de Medios y Generación de Visuales...")
        img_out = os.path.join(self.temp_dir, "qa_placeholder.jpg")
        
        # Test de generación de imagen con fallback visual seguro
        res_img = self.media._generate_fallback_image(img_out, "Prueba QA", 540, 960)
        self.assertTrue(os.path.exists(res_img), "La imagen de respaldo no se creó")
        self.assertGreater(os.path.getsize(res_img), 5000, "El tamaño de la imagen es inválido")
        print("  -> OK: Generador visual seguro calibrado a 540x960.")

    # -------------------------------------------------------------
    # 5. QA TEST: MOTOR DE MOVIMIENTO & COLOR GRADING (VFX)
    # -------------------------------------------------------------
    def test_05_motion_engine(self):
        print("\n[QA TEST 5] Verificando Efectos de Cámara Ken Burns y Graduación de Color...")
        img_src = os.path.join(self.temp_dir, "qa_placeholder.jpg")
        if not os.path.exists(img_src):
            self.media._generate_fallback_image(img_src, "Prueba QA", 540, 960)

        clip_out = os.path.join(self.temp_dir, "qa_clip_motion.mp4")
        
        t0 = time.time()
        res_clip = self.motion.create_directed_scene_clip(
            image_path=img_src,
            duration=2.5,
            output_clip_path=clip_out,
            width=540,
            height=960,
            camera_motion="fast_punch_zoom",
            visual_vfx="deep_vignette"
        )
        elapsed = time.time() - t0
        
        self.assertTrue(os.path.exists(res_clip), "El clip con cámara de IA no se generó")
        self.assertGreater(os.path.getsize(res_clip), 10000, "El clip está corrupto o vacío")
        print(f"  -> OK: Clip con Punch Zoom y Deep Vignette renderizado en {elapsed:.2f}s.")

    # -------------------------------------------------------------
    # 6. QA TEST: DISEÑO SONORO, SFX & SIDECHAIN AUTO-DUCKING
    # -------------------------------------------------------------
    def test_06_audio_engine_ducking(self):
        print("\n[QA TEST 6] Verificando Masterización Vocal, SFX y Sidechain Auto-Ducking...")
        voice_in = os.path.join(self.temp_dir, "qa_voice.mp3")
        if not os.path.exists(voice_in):
            self.tts.synthesize(text="Prueba de audio para sidechain.", output_path=voice_in, voice="es-MX-JorgeNeural")

        audio_out = os.path.join(self.temp_dir, "qa_audio_mixed.mp3")
        
        # Test mix directed con sidechain y cues de sonido
        t0 = time.time()
        final_audio = self.audio.mix_directed_audio(
            voice_path=voice_in,
            output_mixed_path=audio_out,
            total_duration=3.5,
            scene_durations=[2.0, 1.5],
            sfx_cues=["impact_boom", "whoosh_fast"],
            music_path=None, # Solo voz + SFX sintetizados
            music_volume=0.10
        )
        elapsed = time.time() - t0
        
        self.assertTrue(os.path.exists(final_audio), "El audio final mezclado no existe")
        self.assertGreater(os.path.getsize(final_audio), 5000, "El audio está corrupto")
        print(f"  -> OK: Pista masterizada con SFX sincronizados producida en {elapsed:.2f}s.")

    # -------------------------------------------------------------
    # 7. QA TEST: SUBTÍTULOS KINÉTICOS (HORMOZI BOUNCY POP)
    # -------------------------------------------------------------
    def test_07_subtitles_engine_ass(self):
        print("\n[QA TEST 7] Verificando Subtítulos Kinéticos Pop Elásticos...")
        ass_out = os.path.join(self.temp_dir, "qa_subtitles.ass")
        
        from src.core.tts_engine import WordTiming
        dummy_words = [
            WordTiming("ESTE", 0.0, 0.4),
            WordTiming("SECRETO", 0.4, 0.9),
            WordTiming("CAMBIA", 0.9, 1.4),
            WordTiming("TODO", 1.4, 2.0)
        ]
        dummy_scenes = [
            Scene(scene_id=1, text="Este secreto cambia todo.", image_prompt="", keywords=["secreto"], subtitle_mode="dynamic_pop", accent_color="&H0000E5FF&")
        ]
        
        res_ass = self.subtitles.generate_directed_ass(
            words=dummy_words,
            output_ass_path=ass_out,
            scenes=dummy_scenes,
            scene_durations=[2.0],
            width=1080,
            height=1920,
            font_size=64,
            position="bottom"
        )
        
        self.assertTrue(os.path.exists(res_ass), "El archivo .ass no fue generado")
        with open(res_ass, "r", encoding="utf-8") as f:
            ass_content = f.read()
        
        # Verificar que incluya las etiquetas de animación elástica y estilos de alto impacto
        self.assertIn(r"\fscx122", ass_content, "Debe contener el efecto Pop elástico")
        self.assertIn("Style: Default", ass_content, "Debe contener estilo base con borde reforzado")
        self.assertIn("140", ass_content, "Debe respetar la zona segura de 140px para botones de TikTok")
        print("  -> OK: Formato ASS Hormozi validado con zona segura y animación elástica.")

    # -------------------------------------------------------------
    # 8. QA TEST: ASISTENTE COPILOT DE EDICIÓN EN LENGUAJE NATURAL
    # -------------------------------------------------------------
    def test_08_editor_copilot(self):
        print("\n[QA TEST 8] Verificando Asistente Copilot de Edición por Texto...")
        sample_state = {
            "title": "Video Original",
            "voice": "es-MX-JorgeNeural",
            "voice_rate": "+0%",
            "subtitle_style": "yellow_pop",
            "music_volume": 0.12,
            "scenes": [
                {"scene_id": 1, "text": "Escena 1", "media_type": "ai_image"},
                {"scene_id": 2, "text": "Escena 2", "media_type": "ai_image"}
            ]
        }
        
        # Comando 1: Cambiar subtítulos a verde
        res1, _ = self.copilot.edit_project(sample_state, "Pon los subtítulos en verde neón")
        self.assertEqual(res1["subtitle_style"], "green_neon")
        
        # Comando 2: Acelerar la voz
        res2, _ = self.copilot.edit_project(res1, "Acelera la voz un 10 por ciento")
        self.assertIn(res2.get("voice_rate"), ["+10%", "+12%", "+15%"])
        
        # Comando 3: Cambiar volumen de música
        res3, _ = self.copilot.edit_project(res2, "Baja el volumen de la música a la mitad")
        self.assertAlmostEqual(res3["music_volume"], 0.06, places=2)
        
        print("  -> OK: Copilot interpreta comandos y modifica el estado del proyecto con precisión.")

    # -------------------------------------------------------------
    # 9. QA TEST: RENDER FINAL SINCRONIZADO
    # -------------------------------------------------------------
    def test_09_video_renderer_assembly(self):
        print("\n[QA TEST 9] Verificando render final con transición y audio sincronizados...")
        img_a = os.path.join(self.temp_dir, "qa_a.jpg")
        img_b = os.path.join(self.temp_dir, "qa_b.jpg")
        self.media._generate_fallback_image(img_a, "escena A", 540, 960)
        self.media._generate_fallback_image(img_b, "escena B", 540, 960)
        voice_in = os.path.join(self.temp_dir, "qa_voice.mp3")
        if not os.path.exists(voice_in):
            self.tts.synthesize(text="Prueba de audio para el render final.", output_path=voice_in)
        from src.core.tts_engine import get_media_duration
        total = round(get_media_duration(voice_in) * 30) / 30
        durations = [round(total / 2 * 30) / 30]
        durations.append(total - durations[0])
        scenes = [
            Scene(scene_id=1, text="A", image_prompt="", keywords=[], transition_out="crossfade"),
            Scene(scene_id=2, text="B", image_prompt="", keywords=[], camera_motion="pan_left"),
        ]
        out = os.path.join(self.temp_dir, "qa_final.mp4")
        renderer = VideoRenderer(width=540, height=960, fps=30, render_profile="draft")
        t0 = time.time()
        renderer.render_directed_reel(
            scene_images=[img_a, img_b], scene_durations=durations, scenes=scenes,
            audio_path=voice_in, subtitles_path=None, output_video_path=out,
            total_duration=total, motion_engine=self.motion, width=540, height=960,
            work_dir=self.temp_dir,
        )
        elapsed = time.time() - t0
        self.assertTrue(os.path.exists(out), "El video final no se generó")
        self.assertAlmostEqual(get_media_duration(out), total, delta=0.15, msg="El video debe durar lo mismo que el audio")
        print(f"  -> OK: Render con crossfade sincronizado en {elapsed:.2f}s.")


if __name__ == "__main__":
    unittest.main()
