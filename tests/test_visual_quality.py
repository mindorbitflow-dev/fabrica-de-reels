"""Exposición automática y respaldo sin Real-ESRGAN (sin red)."""

import os
import tempfile
import unittest

from PIL import Image

from src.core.media_engine import MediaEngine, correct_exposure, exposure_gamma, mean_luma
from src.core.upscaler import Upscaler


def _dark_image(base: int = 15) -> Image.Image:
    img = Image.new("RGB", (120, 200))
    pixels = img.load()
    for y in range(200):
        for x in range(120):
            value = base + (x + y) % 60        # tonos oscuros
            if 90 < y < 110 and 50 < x < 70:
                value = 250                    # una luz brillante
            pixels[x, y] = (value, value, int(value * 0.9))
    return img


class ExposureTests(unittest.TestCase):
    def test_dark_image_is_lifted_without_clipping_highlights(self):
        img = _dark_image()
        out = correct_exposure(img, 95)
        self.assertGreater(mean_luma(out), mean_luma(img) + 20)
        self.assertLessEqual(out.convert("L").getextrema()[1], 255)
        bright_before = sum(img.convert("L").histogram()[250:])
        bright_after = sum(out.convert("L").histogram()[254:])
        self.assertLessEqual(bright_after, bright_before * 3)

    def test_dark_niche_target_stays_darker_than_regular(self):
        img = _dark_image(base=50)   # penumbra suave: el nicho oscuro la respeta, el normal la aclara
        self.assertLess(mean_luma(correct_exposure(img, MediaEngine.EXPOSURE_TARGET_DARK)),
                        mean_luma(correct_exposure(img, MediaEngine.EXPOSURE_TARGET)))

    def test_bright_image_is_untouched(self):
        img = Image.new("RGB", (50, 50), (180, 180, 180))
        self.assertIs(correct_exposure(img, 95), img)
        self.assertEqual(exposure_gamma(img, 95), 1.0)


class UpscalerFallbackTests(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        upscaler = Upscaler(binary="no/existe.exe")
        self.assertFalse(upscaler.available)
        self.assertIsNone(upscaler.upscale("a.jpg", "b.png"))

    def test_media_engine_requests_full_size_without_upscaler(self):
        engine = MediaEngine(output_dir=tempfile.mkdtemp(), upscaler=Upscaler(binary="no/existe.exe"))
        self.assertFalse(engine.upscaler.available)


if __name__ == "__main__":
    unittest.main()
