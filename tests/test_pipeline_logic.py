"""Pruebas sin red de la lógica de sincronía, transiciones, proveedores LLM y lotes."""

import json
import os
import tempfile
import unittest

from src.batch import BatchItem, batch_job_id, parse_batch_file
from src.core.llm_client import is_auth_error
from src.core.script_generator import ReelScript, Scene, ScriptGenerator
from src.core.tts_engine import TTSEngine, WordTiming
from src.core.video_renderer import VideoRenderer, quantize_durations
from src.pipeline import calculate_scene_durations, sentence_cuts


def _script(hook, *texts):
    scenes = [Scene(i + 1, t, "img", []) for i, t in enumerate(texts)]
    return ReelScript("t", "n", hook, scenes, "", " ".join([hook, *texts]))


class SceneTimingTests(unittest.TestCase):
    def test_cuts_land_in_the_silence_between_scenes(self):
        script = _script("Hola mundo.", "Uno dos.", "Tres cuatro.")
        tokens = "Hola mundo. Uno dos. Tres cuatro.".split()
        times = [(0.0, 0.4), (0.5, 0.9), (1.5, 1.8), (1.9, 2.2), (3.0, 3.3), (3.4, 3.8)]
        words = [WordTiming(t, s, e, i) for i, (t, (s, e)) in enumerate(zip(tokens, times))]
        durations = calculate_scene_durations(script, 4.0, words)
        # Escena 1 = hook + "Uno dos." -> corte a mitad del silencio 2.2..3.0
        self.assertAlmostEqual(durations[0], 2.6)
        self.assertAlmostEqual(durations[1], 1.4)
        self.assertAlmostEqual(sum(durations), 4.0)

    def test_sentence_cuts_follow_each_scene_sentence(self):
        script = _script("Hola.", "Uno dos. Tres.", "Cuatro.")
        tokens = "Hola. Uno dos. Tres. Cuatro.".split()
        times = [(0.0, 0.4), (0.6, 0.9), (1.0, 1.3), (1.9, 2.3), (3.0, 3.4)]
        words = [WordTiming(t, s, e, i) for i, (t, (s, e)) in enumerate(zip(tokens, times))]
        cuts = sentence_cuts(script, words)
        self.assertEqual(len(cuts), 2)
        self.assertAlmostEqual(cuts[0][0], 1.6)   # entre "dos." y "Tres."
        self.assertEqual(cuts[1], [])
        self.assertEqual(sentence_cuts(script, []), [[None], []])

    def test_falls_back_to_proportional_without_alignment(self):
        script = _script("A b.", "c d e f.")
        durations = calculate_scene_durations(script, 6.0, [WordTiming("x", 0, 1)])
        self.assertEqual(len(durations), 1)
        self.assertAlmostEqual(durations[0], 6.0)

    def test_quantized_durations_keep_total_and_never_drift(self):
        raw = [1.234, 2.345, 3.456, 0.01, 4.0]
        q = quantize_durations(raw, 30)
        self.assertAlmostEqual(sum(q), round(sum(raw) * 30) / 30, places=6)
        for d in q:
            self.assertGreater(d, 0)
            self.assertAlmostEqual(d * 30, round(d * 30), places=6)

    def test_punctuation_is_reattached_to_edge_words(self):
        text = "¿Sabías que el pulpo tiene tres corazones? Sí, tres."
        raw = [WordTiming(w, i, i + 0.5) for i, w in enumerate(["Sabías", "que", "el", "pulpo", "tiene", "tres", "corazones", "Sí", "tres"])]
        words = TTSEngine._attach_punctuation(text, raw)
        self.assertEqual(words[0].word, "¿Sabías")
        self.assertEqual(words[6].word, "corazones?")
        self.assertEqual(words[7].word, "Sí,")
        self.assertEqual([w.token_index for w in words], list(range(9)))


class SubtitleTests(unittest.TestCase):
    def test_events_never_overlap_and_short_gaps_are_filled(self):
        from src.core.subtitles_engine import SubtitlesEngine, format_ass_time
        events = [(0.10, 0.37, "P", "a"), (0.26, 0.48, "P", "b"), (0.60, 0.90, "P", "c"), (2.0, 2.4, "P", "d")]
        resolved = SubtitlesEngine._resolve_overlaps(events)
        for (s1, e1, *_), (s2, *_rest) in zip(resolved, resolved[1:]):
            self.assertLessEqual(e1, s2)
        self.assertEqual(resolved[1][1], 0.60)   # hueco corto rellenado
        self.assertEqual(resolved[2][1], 0.90)   # silencio largo respetado
        self.assertEqual(format_ass_time(1.996), "0:00:02.00")
        self.assertEqual(format_ass_time(3725.5), "1:02:05.50")


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.renderer = VideoRenderer(fps=30)

    def test_pads_only_for_real_transitions_and_never_on_last_clip(self):
        pads = self.renderer.transition_pads(["crossfade", "smash_cut", "glitch", "crossfade"], [3.0, 3.0, 3.0, 3.0])
        self.assertAlmostEqual(pads[0], round(0.35 * 30) / 30)
        self.assertEqual(pads[1], 0.0)
        self.assertGreater(pads[2], 0)
        self.assertEqual(pads[3], 0.0)

    def test_pad_is_capped_for_very_short_scenes(self):
        pads = self.renderer.transition_pads(["crossfade", "smash_cut"], [0.5, 0.5])
        self.assertLessEqual(pads[0], 0.2 + 1e-9)

    def test_graph_offsets_equal_narrative_cut_times(self):
        graph = self.renderer._transition_graph(3, [2.0, 3.0, 1.0], [0.3, 0.0, 0.0], ["crossfade", "smash_cut", "smash_cut"])
        self.assertIn("xfade=transition=fade:duration=0.3000:offset=2.0000", graph)
        self.assertIn("concat=n=2:v=1:a=0[vx]", graph)

    def test_too_many_clips_disable_graph(self):
        renderer = VideoRenderer(fps=30, max_graph_clips=3)
        self.assertEqual(renderer.transition_pads(["crossfade"] * 4, [2.0] * 4), [0.0] * 4)


class _FakeAuthError(Exception):
    status_code = 401


class _FakeRouter:
    """Router falso: 'custom' tiene credenciales inválidas; 'gemini' responde el guion dado."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.disabled = set()

    def available_providers(self, preferred="auto"):
        return [p for p in ("custom", "gemini") if p not in self.disabled]

    @property
    def has_any(self):
        return True

    def complete(self, prompt, provider, **kwargs):
        self.calls.append((provider, "CORRECCIÓN" in prompt))
        if provider == "custom":
            raise _FakeAuthError("User not found")
        return self.responses.pop(0)

    def handle_error(self, provider, error):
        if is_auth_error(error):
            self.disabled.add(provider)


def _llm_json(scene_words: int, scenes: int = 3) -> str:
    return json.dumps({
        "title": "T", "hook": "Gancho breve.",
        "scenes": [{"scene_id": i + 1, "text": f"Escena {i} " + " ".join(f"palabra{i}x{j}" for j in range(scene_words))} for i in range(scenes)],
    })


class ProviderCascadeTests(unittest.TestCase):
    def test_auth_error_skips_provider_and_accepts_valid_script(self):
        generator = ScriptGenerator()
        generator.llm = _FakeRouter([_llm_json(9)])  # 15s -> 33 palabras, 3 escenas
        script = generator.generate("tema", "ciencia", target_duration=15)
        self.assertEqual(script.source, "gemini")
        self.assertEqual(generator.llm.calls, [("custom", False), ("gemini", False)])

    def test_off_contract_script_gets_one_correction_retry(self):
        generator = ScriptGenerator()
        generator.llm = _FakeRouter([_llm_json(40), _llm_json(9)])
        script = generator.generate("tema", "ciencia", target_duration=15)
        self.assertEqual(script.source, "gemini")
        self.assertEqual(generator.llm.calls[-1], ("gemini", True))

    def test_everything_failing_ends_in_offline(self):
        generator = ScriptGenerator()
        generator.llm = _FakeRouter([_llm_json(80), _llm_json(80)])
        script = generator.generate("tema", "ciencia", target_duration=15)
        self.assertEqual(script.source, "offline")


class BatchTests(unittest.TestCase):
    def test_parse_txt_with_optional_columns(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as file:
            file.write("# comentario\nTema uno\nTema dos | finanzas | 45\n\n")
        try:
            items = parse_batch_file(file.name, "ciencia", 30)
        finally:
            os.remove(file.name)
        self.assertEqual(items, [BatchItem("Tema uno", "ciencia", 30), BatchItem("Tema dos", "finanzas", 45)])

    def test_job_id_is_stable_for_resume(self):
        self.assertEqual(batch_job_id("lote", 0, "Pulpos: 3 corazones"), batch_job_id("lote", 0, "Pulpos: 3 corazones"))
        self.assertTrue(batch_job_id("lote", 4, "Tema").startswith("lote_005_"))


if __name__ == "__main__":
    unittest.main()
