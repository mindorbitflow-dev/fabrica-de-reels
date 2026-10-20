"""Pruebas sin red de la publicación: idempotencia, bloqueo de videos dudosos y textos."""

import json
import os
import tempfile
import unittest
from unittest import mock

from src.core import publisher


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.video = os.path.join(self.dir, "v.mp4")
        open(self.video, "wb").write(b"x")
        self.package = os.path.join(self.dir, "publicacion.json")
        self._write({"video": self.video, "title": "T", "caption_completo": "Texto #a", "hashtags": ["#a"],
                     "fuentes_y_creditos": "Imágenes:\n• Foto CC BY", "script_source": "gemini"})

    def _write(self, data, name="publicacion.json"):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read(self):
        with open(self.package, encoding="utf-8") as f:
            return json.load(f)

    def test_retry_only_uploads_networks_that_failed(self):
        with mock.patch.object(publisher, "publish_instagram", return_value="https://ig/1") as ig, \
             mock.patch.object(publisher, "publish_facebook", side_effect=RuntimeError("caído")) as fb:
            publisher.publish_package(self.package, ["instagram", "facebook"])
            self.assertFalse(self._read()["publicado"]["facebook"]["ok"])
            fb.side_effect, fb.return_value = None, "https://fb/1"
            done = publisher.publish_package(self.package, ["instagram", "facebook"])
        self.assertEqual(ig.call_count, 1)
        self.assertEqual(fb.call_count, 2)
        self.assertEqual(done["facebook"]["url"], "https://fb/1")
        self.assertTrue(self._read()["publicado"]["instagram"]["ok"])

    def test_generic_or_placeholder_videos_are_not_auto_published(self):
        self.assertIsNone(publisher.auto_publish_blocker(self.package))
        self._write({"warnings": [{"step": "media", "message": "Escena 2: imagen de respaldo (timeout)."}]}, "manifest.json")
        self.assertIn("respaldo", publisher.auto_publish_blocker(self.package))
        data = self._read()
        data["script_source"] = "offline"
        self._write(data)
        with mock.patch.object(publisher, "connected_platforms", return_value=["instagram"]), \
             mock.patch.object(publisher, "publish_instagram") as ig:
            results, skipped = publisher.auto_publish(self.package, {"auto": True, "platforms": ["instagram"]})
        ig.assert_not_called()
        self.assertEqual(results, {})
        self.assertIn("genérico", skipped)

    def test_nothing_happens_without_connected_accounts(self):
        with mock.patch.object(publisher, "connected_platforms", return_value=[]):
            self.assertEqual(publisher.auto_publish(self.package, {"auto": True}), ({}, None))

    def test_post_text_keeps_credits_and_respects_limit(self):
        package = self._read()
        self.assertIn("CC BY", publisher.post_text(package, 2200))
        self.assertLessEqual(len(publisher.post_text(package, 10)), 10)

    def test_posts_go_to_meta_only_and_skip_placeholder_images(self):
        data = self._read()
        data.update(type="post", image=self.video)
        self._write(data)
        with mock.patch.object(publisher, "connected_platforms", return_value=["instagram", "facebook", "youtube"]):
            self.assertEqual(publisher.enabled_platforms({}, "post"), ["instagram", "facebook"])
            with mock.patch.object(publisher, "publish_instagram_image", return_value="https://ig/p") as ig, \
                 mock.patch.object(publisher, "publish_facebook_photo", return_value="https://fb/p") as fb, \
                 mock.patch.object(publisher, "publish_youtube") as yt:
                done = publisher.publish_package(self.package, ["instagram", "facebook", "youtube"])
        ig.assert_called_once()
        fb.assert_called_once()
        yt.assert_not_called()
        self.assertNotIn("youtube", done)
        data["image_fallback"] = True
        self._write(data)
        self.assertIn("respaldo", publisher.auto_publish_blocker(self.package))

    def test_youtube_upload_declares_synthetic_media(self):
        request = mock.Mock()
        request.next_chunk.return_value = (None, {"id": "abc"})
        youtube = mock.Mock()
        youtube.videos.return_value.insert.return_value = request
        with mock.patch.object(publisher, "_youtube", return_value=youtube), \
             mock.patch("googleapiclient.http.MediaFileUpload"):
            url = publisher.publish_youtube(self._read(), "unlisted")
        body = youtube.videos.return_value.insert.call_args.kwargs["body"]
        self.assertEqual(url, "https://youtube.com/shorts/abc")
        self.assertEqual(body["status"]["privacyStatus"], "unlisted")
        self.assertTrue(body["status"]["containsSyntheticMedia"])
        self.assertEqual(body["snippet"]["tags"], ["a"])


if __name__ == "__main__":
    unittest.main()
