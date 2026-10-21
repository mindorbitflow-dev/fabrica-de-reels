"""Noticias (sin red): lectura del RSS, texto del artículo y elección de la noticia."""

import datetime
import unittest

from src.core.news import Story, article_body, fresh_stories, parse_feed

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


if __name__ == "__main__":
    unittest.main()
