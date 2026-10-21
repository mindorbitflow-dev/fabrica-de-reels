"""Noticias (sin red): lectura del RSS, texto del artículo y elección de la noticia."""

import datetime
import os
import tempfile
import unittest
from unittest import mock

from src.core.media_engine import MediaEngine
from src.core.news import COLOMBIA_PHOTO, FOREIGN_PHOTO, Story, article_body, fresh_stories, parse_feed

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 20, 18, 0, tzinfo=UTC)

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Congreso aprueba el presupuesto de 2027</title><link>https://a.co/1</link>
<pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate><description>&lt;p&gt;Resumen&lt;/p&gt;</description></item>
<item><title>Sin fecha</title><link>https://a.co/2</link></item>
<item><title>Inundaciones en Riohacha</title><link>https://a.co/3</link><pubDate>2026-10-20T10:51:15-05:00</pubDate></item>
</channel></rss>"""


def story(title, hours_ago, url="https://x.co"):
    return Story(title, url, "El Tiempo", NOW - datetime.timedelta(hours=hours_ago))


class NewsTests(unittest.TestCase):
    def test_feed_keeps_items_with_date_in_both_formats(self):
        stories = parse_feed(RSS, "El Tiempo")
        self.assertEqual([s.title for s in stories], ["Congreso aprueba el presupuesto de 2027", "Inundaciones en Riohacha"])
        self.assertEqual(stories[0].summary, "Resumen")
        self.assertEqual(stories[1].published, datetime.datetime(2026, 10, 20, 15, 51, 15, tzinfo=UTC))

    def test_article_body_comes_from_json_ld(self):
        html = ('<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
                '<script type="application/ld+json">[{"@type":"NewsArticle","articleBody":"Uno <b>dos</b>&nbsp;\\n tres"}]</script>')
        self.assertEqual(article_body(html), "Uno dos tres")
        self.assertEqual(article_body("<p>sin datos</p>"), "")

    def test_fresh_stories_skip_old_routine_published_and_duplicates(self):
        stories = [
            story("Congreso aprueba el presupuesto de 2027", 1),
            story("Así rotará el Pico y Placa en Bogotá este martes", 0),
            story("Incendio forestal en el Tolima", 20),
            story("El Congreso aprobó el presupuesto 2027 en último debate", 2),
            story("Gobierno anuncia nuevo metro para Medellín", 3),
            story("Inundaciones en Riohacha dejan 300 familias afectadas", 4),
        ]
        fresh = fresh_stories(stories, ["Inundaciones en Riohacha: 300 familias afectadas"], NOW)
        self.assertEqual([s.title for s in fresh],
                         ["Congreso aprueba el presupuesto de 2027", "Gobierno anuncia nuevo metro para Medellín"])

    def test_photo_must_be_described_as_colombian(self):
        self.assertTrue(COLOMBIA_PHOTO.search("Historic architecture of the National Capitol in Bogotá"))
        self.assertTrue(COLOMBIA_PHOTO.search("A Colombian policewoman patrolling a square"))
        self.assertFalse(COLOMBIA_PHOTO.search("Senate desks with flags in California"))
        self.assertFalse(COLOMBIA_PHOTO.search("The US Capitol building in Washington"))

    def test_thematic_photo_must_not_be_placed_abroad(self):
        self.assertTrue(FOREIGN_PHOTO.search("The US Capitol building in Washington"))
        self.assertTrue(FOREIGN_PHOTO.search("Soldiers marching in Mexico City"))
        self.assertFalse(FOREIGN_PHOTO.search("Calculator and budget documents on a desk"))
        self.assertFalse(FOREIGN_PHOTO.search("Soldiers on patrol in the jungle"))

    def test_fetch_photo_skips_used_and_foreign_photos(self):
        photos = [{"id": 1, "alt": "Budget documents", "src": {"large2x": "u1"}},
                  {"id": 2, "alt": "Capitol in Washington", "src": {"large2x": "u2"}},
                  {"id": 3, "alt": "Calculator on a desk", "src": {"large2x": "u3"}}]
        response = mock.Mock(status_code=200, json=lambda: {"photos": photos})
        engine = MediaEngine.__new__(MediaEngine)
        downloaded = []
        with tempfile.TemporaryDirectory() as folder,                 mock.patch.dict(os.environ, {"PEXELS_API_KEY": "k"}),                 mock.patch("src.core.media_engine.requests.get", return_value=response),                 mock.patch.object(MediaEngine, "_download", lambda self, url, tmp: downloaded.append(url) or open(tmp, "wb").close() or True),                 mock.patch.object(MediaEngine, "_normalize_image", lambda self, tmp, w, h, exposure_target, save_to: None):
            result = engine.fetch_photo("budget documents", os.path.join(folder, "foto.jpg"), 1080, 1350,
                                        alt_must_not_match=FOREIGN_PHOTO, exclude_ids={"pexels:1"})
        self.assertEqual(downloaded, ["u3"])
        self.assertEqual(result.source_id, "pexels:3")


if __name__ == "__main__":
    unittest.main()
