"""Fase 1 de producción: alineación de voz, texto animado, cortes en pausas y audio (sin red)."""

import unittest
from types import SimpleNamespace

from src.core.aligner import align_words
from src.core.production import plan_shots
from src.core.subtitles_engine import pick_pop_words
from src.core.tts_engine import WordTiming, spoken_tokens
from src.core.voice_gemini import split_for_tts


def _heard(pairs):
    return [WordTiming(w, s, s + 0.3) for w, s in pairs]


class AlignerTests(unittest.TestCase):
    def test_leading_instruction_is_detected_and_script_keeps_punctuation(self):
        script = "Nacen grises. ¿Cómo ocurre?"
        heard = _heard([("Narra", 0.0), ("con", 0.4), ("tono", 0.7), ("Nacen", 2.0), ("grises", 2.4), ("Cómo", 3.2), ("ocurre", 3.6)])
        result = align_words(spoken_tokens(script), heard)
        self.assertEqual(result.coverage, 1.0)
        self.assertAlmostEqual(result.extra_leading_seconds, 2.0)
        self.assertEqual([w.word for w in result.words], ["Nacen", "grises.", "¿Cómo", "ocurre?"])
        self.assertEqual([w.token_index for w in result.words], [0, 1, 2, 3])

    def test_misheard_words_get_interpolated_times(self):
        script = "El pulpo tiene tres corazones"
        heard = _heard([("El", 0.0), ("pulpo", 0.4), ("tiemne", 0.8), ("tres", 1.2), ("corazones", 1.6)])
        result = align_words(spoken_tokens(script), heard)
        self.assertLess(result.coverage, 1.0)
        tiene = result.words[2]
        self.assertGreater(tiene.start, result.words[1].start)
        self.assertLess(tiene.start, result.words[3].start)

    def test_accents_and_case_do_not_break_matching(self):
        result = align_words(spoken_tokens("Óscar llegó."), _heard([("oscar", 0.0), ("LLEGO", 0.5)]))
        self.assertEqual(result.coverage, 1.0)

    def test_low_coverage_when_voice_skips_text(self):
        result = align_words(spoken_tokens("uno dos tres cuatro cinco seis"), _heard([("uno", 0.0), ("dos", 0.3)]))
        self.assertLess(result.coverage, 0.85)


class TtsChunkTests(unittest.TestCase):
    def test_long_text_is_split_on_sentence_boundaries(self):
        text = " ".join(f"Frase número {i} con algo de texto." for i in range(60))
        chunks = split_for_tts(text, limit=300)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 300 for c in chunks))
        self.assertTrue(all(c.endswith(".") for c in chunks))
        self.assertEqual(" ".join(chunks), text)


class OverlayTests(unittest.TestCase):
    def test_keyword_pops_follow_the_voice_and_respect_spacing(self):
        words = [WordTiming(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(
            "Los flamencos nacen grises y comen artemia salina que les da color rosa intenso".split())]
        scenes = [SimpleNamespace(keywords=["grises"]), SimpleNamespace(keywords=["artemia salina"]), SimpleNamespace(keywords=["rosa"])]
        pops = pick_pop_words(scenes, words, min_gap=2.5, skip_until=0.0)
        # "artemia salina" llega 1.5 s después de "grises": se omite para no saturar la pantalla.
        self.assertEqual([p[2] for p in pops], ["grises", "rosa"])
        self.assertAlmostEqual(pops[1][0], 6.0)

    def test_title_window_is_left_free(self):
        words = [WordTiming("tres", 1.0, 1.4)]
        self.assertEqual(pick_pop_words([SimpleNamespace(keywords=["tres"])], words, skip_until=2.6), [])


class CutPointTests(unittest.TestCase):
    def test_internal_cuts_snap_to_voice_pauses(self):
        scene = SimpleNamespace(camera_motion="slow_dolly_in", visual_vfx="cinematic_clean", media_type="pexels_video", transition_out="smash_cut")
        shots = plan_shots([scene], [9.0], [["a.mp4", "b.mp4", "c.mp4"]], shot_seconds=3.0, fps=30, cut_points=[3.4, 5.7])
        cuts = [sum(s.duration for s in shots[:k]) for k in range(1, len(shots))]
        self.assertAlmostEqual(cuts[0], 3.4, places=2)
        self.assertAlmostEqual(cuts[1], 5.7, places=2)
        self.assertAlmostEqual(sum(s.duration for s in shots), 9.0)
        self.assertEqual([s.media_path for s in shots], ["a.mp4", "b.mp4", "c.mp4"])

    def test_each_sentence_shows_its_own_clip(self):
        scene = SimpleNamespace(camera_motion="slow_dolly_in", visual_vfx="cinematic_clean", media_type="pexels_video", transition_out="crossfade")
        shots = plan_shots([scene], [9.0], [["a.mp4", "b.mp4", "c.mp4"]], shot_seconds=3.0, fps=30,
                           cut_points=[1.0, 2.5, 6.1], sentence_cuts=[[2.2, 6.4]])
        self.assertEqual([s.media_path for s in shots], ["a.mp4", "b.mp4", "c.mp4"])
        self.assertAlmostEqual(shots[0].duration, 2.2, places=1)
        self.assertAlmostEqual(sum(s.duration for s in shots), 9.0)
        self.assertEqual(shots[-1].transition_out, "crossfade")

    def test_short_sentence_joins_the_previous_shot_and_keeps_order(self):
        scene = SimpleNamespace(camera_motion="pan_left", visual_vfx="cinematic_clean", media_type="pexels_video", transition_out="smash_cut")
        shots = plan_shots([scene], [8.0], [["a.mp4", "b.mp4", "c.mp4"]], shot_seconds=3.0, fps=30,
                           sentence_cuts=[[1.0, None]])
        self.assertEqual([s.media_path for s in shots], ["a.mp4"])
        shots = plan_shots([scene], [8.0], [["a.mp4", "b.mp4", "c.mp4"]], shot_seconds=3.0, fps=30,
                           sentence_cuts=[[1.0, 4.0]])
        self.assertEqual([s.media_path for s in shots], ["a.mp4", "c.mp4"])


if __name__ == "__main__":
    unittest.main()
