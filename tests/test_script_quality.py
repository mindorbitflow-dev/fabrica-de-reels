"""Pruebas aisladas del contrato editorial de los guiones.

No llaman APIs ni renderizan video: deben poder ejecutarse antes de cualquier lote.
"""

import json
import unittest

from src.core.director_skills import build_director_prompt
from src.core.script_generator import ReelScript, Scene, ScriptGenerator
from src.core.script_quality import WORDS_PER_SECOND, assess_script, create_script_plan


class ScriptQualityTests(unittest.TestCase):
    def setUp(self):
        self.generator = ScriptGenerator()

    def test_duration_plans_are_speech_based(self):
        expected = {15: (3, 33), 30: (5, 66), 60: (8, 132), 300: (20, 660), 600: (30, 1320)}
        for duration, (scenes, words) in expected.items():
            with self.subTest(duration=duration):
                plan = create_script_plan(duration)
                self.assertEqual(plan.scene_count, scenes)
                self.assertEqual(plan.target_words, words)
                self.assertEqual(sum(plan.scene_word_budgets), words)

    def test_words_per_second_matches_measured_voice(self):
        # Medido sobre las locuciones reales de Edge-TTS (2.0-2.4 palabras/seg).
        self.assertTrue(2.0 <= WORDS_PER_SECOND <= 2.4)

    def test_offline_fallback_respects_duration_and_no_duplicate_scenes(self):
        for duration in (10, 15, 30, 45, 60, 120, 300, 600):
            with self.subTest(duration=duration):
                script = self.generator._generate_offline(
                    topic="Por qué los pulpos tienen tres corazones",
                    niche="ciencia",
                    target_duration=duration,
                )
                assessment = assess_script(
                    script.full_script_text,
                    (scene.text for scene in script.scenes),
                    create_script_plan(duration),
                )
                self.assertTrue(assessment.strict_ok, assessment.issues)

    def test_forced_editorial_formula_is_flagged(self):
        plan = create_script_plan(30, scene_count=2)
        text = "Durante años nos ocultaron esto. La conclusión indiscutible cambia todo."
        assessment = assess_script(text, [text, "Otra idea diferente."], plan)
        self.assertGreater(assessment.forced_editorial_pattern_count, 0)

    def test_short_remote_answer_is_not_accepted_for_a_long_video(self):
        short_script = ReelScript(
            title="Respuesta corta",
            niche="finanzas",
            hook="Una idea breve.",
            scenes=[Scene(1, "Esta explicación no alcanza para cinco minutos.", "visual", ["prueba"])],
            cta="",
            full_script_text="Una idea breve. Esta explicación no alcanza para cinco minutos.",
        )
        accepted = self.generator._ensure_script_contract(
            short_script,
            topic="Cómo funciona el interés compuesto",
            niche="finanzas",
            niche_data={},
            target_duration=300,
            scene_count=None,
        )
        assessment = assess_script(
            accepted.full_script_text,
            (scene.text for scene in accepted.scenes),
            create_script_plan(300),
        )
        self.assertTrue(assessment.strict_ok, assessment.issues)

    def test_prompt_requires_natural_closure_and_a_word_budget(self):
        prompt = build_director_prompt("El interés compuesto", {"name": "Finanzas"}, 60)
        self.assertIn("aproximadamente 132 palabras", prompt)
        self.assertIn("exactamente 8", prompt)
        self.assertIn("No fuerces una moraleja", prompt)
        self.assertIn("No inventes expertos", prompt)
        self.assertIn("hashtags", prompt)

    def test_parser_normalizes_llm_output(self):
        raw = """<think>razonando</think> Aquí tienes:
        ```json
        {"title": "T", "hook": "Gancho.", "caption": "Texto", "hashtags": "#uno #dos",
         "scenes": [
            {"scene_id": 7, "text": "Primera.", "camera_motion": "inventado", "accent_color": "rojo"},
            {"scene_id": 9, "text": "  "},
            {"scene_id": 3, "text": "Segunda.", "media_type": "pexels_video", "pexels_query": "ocean"}
         ]}
        ```"""
        script = self.generator._parse_llm_json(raw, "tema", "ciencia")
        self.assertEqual([s.scene_id for s in script.scenes], [1, 2])
        self.assertEqual(script.scenes[0].camera_motion, "fast_punch_zoom")
        self.assertEqual(script.scenes[0].accent_color, "&H0000E5FF&")
        self.assertEqual(script.hashtags, ["#Uno", "#Dos"])
        self.assertEqual(script.full_script_text, "Gancho. Primera. Segunda.")

    def test_hook_repeated_in_first_scene_is_not_spoken_twice(self):
        raw = json.dumps({"hook": "Nacen grises.", "scenes": [{"text": "Nacen grises."}, {"text": "Luego cambian."}]})
        script = self.generator._parse_llm_json(raw, "t", "n")
        self.assertEqual(script.full_script_text, "Nacen grises. Luego cambian.")
        raw = json.dumps({"hook": "Nacen grises.", "scenes": [{"text": "Nacen grises. Y rosados al crecer."}, {"text": "Fin."}]})
        script = self.generator._parse_llm_json(raw, "t", "n")
        self.assertEqual(script.full_script_text, "Nacen grises. Y rosados al crecer. Fin.")

    def test_parser_rejects_garbage(self):
        with self.assertRaises(ValueError):
            self.generator._parse_llm_json("no hay json aquí", "tema", "ciencia")

    def test_script_roundtrip_ignores_unknown_fields(self):
        script = self.generator._generate_offline("Tema de prueba", "ciencia", target_duration=20)
        data = script.to_dict()
        data["campo_nuevo"] = 1
        data["scenes"][0]["otro"] = 2
        restored = ReelScript.from_dict(data)
        self.assertEqual(restored.full_script_text, script.full_script_text)
        self.assertEqual(restored.hashtags, script.hashtags)


if __name__ == "__main__":
    unittest.main()
