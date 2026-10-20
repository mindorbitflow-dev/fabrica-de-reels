"""Tema aleatorio, planos por escena, plataformas y adaptación de idioma (sin red)."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from src.core.production import ProductionPolicy, plan_shots
from src.core.script_generator import ReelScript, Scene, ScriptGenerator
from src.core.script_quality import create_script_plan
from src.core.topic_picker import TopicPicker, is_random_topic, is_similar


class TopicPickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.history = os.path.join(self.tmp, "history.json")

    def test_random_words_are_detected(self):
        for word in ("aleatorio", " Aleatorio ", "random", ""):
            self.assertTrue(is_random_topic(word))
        self.assertFalse(is_random_topic("Por qué el cielo es azul"))

    def test_bank_topics_never_repeat_until_exhausted_and_persist(self):
        picker = TopicPicker(history_path=self.history)
        bank = picker.bank("finanzas")
        self.assertEqual(len(bank), 20)
        picks = [picker.pick("finanzas") for _ in range(20)]
        self.assertEqual(len(set(picks)), 20)
        self.assertEqual(len(TopicPicker(history_path=self.history).used_topics("finanzas")), 20)

    def test_every_current_niche_has_a_bank(self):
        import yaml
        with open("config/niches.yaml", encoding="utf-8") as file:
            niches = list(yaml.safe_load(file))
        picker = TopicPicker(history_path=self.history)
        for niche in niches:
            with self.subTest(niche=niche):
                self.assertGreaterEqual(len(picker.bank(niche)), 15)

    def test_yaml_colon_topics_are_kept_as_text(self):
        topics = TopicPicker(history_path=self.history).bank("misterio")
        self.assertIn("El manuscrito Voynich: el libro que nadie puede leer", topics)

    def test_ai_suggestions_skip_used_topics(self):
        picker = TopicPicker(suggest=lambda n, c, a: ["Tema usado otra vez", "Tema nuevo y distinto"], history_path=self.history)
        picker.remember("ciencia", "Tema usado")
        for _ in range(5):
            self.assertEqual(picker.pick("ciencia", remember=False), "Tema nuevo y distinto")

    def test_similarity_ignores_title_filler(self):
        self.assertFalse(is_similar("Cómo funciona una tarjeta de crédito y sus intereses", "Cómo funciona un crédito hipotecario"))
        self.assertTrue(is_similar("La caída de Constantinopla", "La caída de Constantinopla en 1453"))

    def test_generator_resolves_random_topic(self):
        generator = ScriptGenerator()
        generator.topics = TopicPicker(history_path=self.history)
        generator.llm.available_providers = lambda preferred="auto": []
        script = generator.generate("aleatorio", "historia_oculta", target_duration=20)
        self.assertIn(script.title, generator.topics.bank("historia_oculta"))


class ShotPlanTests(unittest.TestCase):
    def test_shots_split_scenes_without_moving_narrative_cuts(self):
        scenes = [SimpleNamespace(camera_motion="slow_dolly_in", visual_vfx="cinematic_clean", media_type="ai_image", transition_out="crossfade"),
                  SimpleNamespace(camera_motion="pan_left", visual_vfx="cinematic_clean", media_type="pexels_video", transition_out="smash_cut")]
        shots = plan_shots(scenes, [10.0, 3.0], [["a.jpg"], ["v1.mp4", "v2.mp4"]], shot_seconds=3.5, fps=30)
        first = [s for s in shots if s.scene_index == 0]
        self.assertEqual(len(first), 3)
        self.assertAlmostEqual(sum(s.duration for s in first), 10.0)
        self.assertEqual(len({s.camera_motion for s in first}), 3)       # misma imagen, cámaras distintas
        self.assertEqual([s.transition_out for s in first], ["smash_cut", "smash_cut", "crossfade"])
        for s in shots:
            self.assertGreaterEqual(s.duration, 2.0)

    def test_short_form_uses_short_shots(self):
        policy = ProductionPolicy.from_config({})
        self.assertLessEqual(policy.target_visual_seconds(75), 4.0)


class LanguageTests(unittest.TestCase):
    def test_english_budget_uses_english_speech_rate(self):
        self.assertEqual(create_script_plan(60, language="en").target_words, 162)
        self.assertEqual(create_script_plan(60, language="es").target_words, 132)

    def test_adapt_keeps_scene_count_and_visuals(self):
        generator = ScriptGenerator()
        script = ReelScript("Pulpos", "ciencia", "Tienen tres corazones.",
                            [Scene(1, "Dos bombean a las branquias.", "octopus gills", ["pulpo"], media_type="pexels_video", pexels_query="octopus"),
                             Scene(2, "El tercero, al cuerpo.", "octopus body", ["pulpo"])], "", "")
        reply = json.dumps({"title": "Octopuses", "hook": "They have three hearts.", "caption": "c", "hashtags": ["#octopus"],
                            "scenes": [{"scene_id": 1, "text": "Two pump blood to the gills."}, {"scene_id": 2, "text": "The third, to the body."}]})
        generator.llm.complete_any = lambda *a, **k: (reply, "gemini")
        adapted = generator.adapt(script, "en")
        self.assertEqual(adapted.language, "en")
        self.assertEqual([s.pexels_query for s in adapted.scenes], ["octopus", ""])
        self.assertEqual(adapted.full_script_text, "They have three hearts. Two pump blood to the gills. The third, to the body.")
        self.assertEqual(script.language, "es")   # el original no se modifica

    def test_adapt_rejects_changed_structure(self):
        generator = ScriptGenerator()
        script = ReelScript("t", "n", "h", [Scene(1, "a", "p", []), Scene(2, "b", "p", [])], "", "")
        generator.llm.complete_any = lambda *a, **k: (json.dumps({"scenes": [{"scene_id": 1, "text": "x"}]}), "gemini")
        with self.assertRaises(ValueError):
            generator.adapt(script, "en")


if __name__ == "__main__":
    unittest.main()
