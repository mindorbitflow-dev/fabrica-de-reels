"""Noticias de Colombia para la página de noticias.

Lee los RSS de medios colombianos, elige la noticia más reciente que aún no se publicó y
saca el texto completo del artículo (el "articleBody" de su JSON-LD, que estos medios publican
para los buscadores). El post se escribe solo con ese texto y cita el medio y el enlace.
"""

from __future__ import annotations

import datetime
import email.utils
import html as html_lib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, List, Optional

import requests

from src.core.researcher import Dossier, Fact
from src.core.topic_picker import is_similar

NEWS_NICHE = "noticias"
FEEDS = {
    "El Tiempo": "https://www.eltiempo.com/rss/colombia.xml",
    "Infobae": "https://www.infobae.com/arc/outboundfeeds/rss/category/colombia/?outputType=xml",
    "El Colombiano": "https://www.elcolombiano.com/rss/colombia.xml",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NoticiasColombia/1.0)"}
MAX_AGE_HOURS = 12
MIN_BODY_WORDS = 120
MAX_BODY_CHARS = 6000
# Una foto de archivo solo ilustra una noticia de aquí si su descripción en Pexels la sitúa en Colombia.
COLOMBIA_PHOTO = re.compile(r"\b(colombia\w*|bogot[aá]|medell[ií]n|cartagena|cali|barranquilla|santa marta|bucaramanga|"
                            r"manizales|pereira|guatap[eé]|villa de leyva|cocora|andes colombianos)\b", re.I)
# Una foto temática (sin Colombia en la descripción) vale si no la sitúa en otro país: el Capitolio de Washington
# no puede ilustrar el Congreso colombiano.
FOREIGN_PHOTO = re.compile(r"\b(united states|usa|u\.s\.?|us capitol|american|america|washington|new york|california|texas|"
                           r"florida|chicago|los angeles|canad\w*|mexic\w*|brazil\w*|brasil\w*|argentin\w*|peru\w*|chile\w*|"
                           r"venezuel\w*|ecuador\w*|spain|spanish|madrid|barcelona|london|british|england|uk|france|french|paris|"
                           r"ital\w*|rome|german\w*|berlin|europe\w*|russia\w*|ukrain\w*|china|chinese|japan\w*|india\w*|"
                           r"israel\w*|africa\w*|australia\w*|white house|parliament|westminster|eiffel)\b", re.I)
# Notas de servicio que se repiten a diario y no son noticia.
ROUTINE = re.compile(r"pico y placa|lotería|loteria|chance|horóscopo|horoscopo|resultados? del|baloto|tasa de cambio|precio del dólar", re.I)


@dataclass
class Story:
    title: str
    url: str
    outlet: str
    published: datetime.datetime
    summary: str = ""


def _parse_date(text: str) -> Optional[datetime.datetime]:
    text = (text or "").strip()
    try:
        date = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            date = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    return date if date.tzinfo else date.replace(tzinfo=datetime.timezone.utc)


def parse_feed(xml: bytes, outlet: str) -> List[Story]:
    stories = []
    for item in ET.fromstring(xml).iter("item"):
        title = re.sub(r"\s+", " ", item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        published = _parse_date(item.findtext("pubDate") or "")
        if title and url and published:
            summary = re.sub(r"<[^>]+>|\s+", " ", item.findtext("description") or "").strip()
            stories.append(Story(title, url, outlet, published, summary))
    return stories


def article_body(html: str) -> str:
    """Texto del artículo según su JSON-LD (NewsArticle.articleBody); vacío si no lo trae."""
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(node, dict) and node.get("articleBody"):
                text = html_lib.unescape(re.sub(r"<[^>]+>", " ", str(node["articleBody"])))
                return re.sub(r"\s+", " ", text).strip()
    return ""


def fresh_stories(stories: Iterable[Story], already_used: List[str], now: datetime.datetime) -> List[Story]:
    """Noticias recientes, no rutinarias y no publicadas ya (titular parecido), de la más nueva a la más vieja."""
    oldest = now - datetime.timedelta(hours=MAX_AGE_HOURS)
    result = []
    for story in sorted(stories, key=lambda s: s.published, reverse=True):
        if story.published < oldest or ROUTINE.search(story.title):
            continue
        if any(is_similar(story.title, used) for used in already_used):
            continue
        if any(is_similar(story.title, s.title) for s in result):
            continue   # la misma noticia en otro medio
        result.append(story)
    return result


class NewsDesk:
    def __init__(self, session: Optional[requests.Session] = None):
        self.http = session or requests.Session()

    def stories(self) -> List[Story]:
        found = []
        for outlet, url in FEEDS.items():
            try:
                response = self.http.get(url, headers=HEADERS, timeout=20)
                response.raise_for_status()
                found += parse_feed(response.content, outlet)
            except (requests.RequestException, ET.ParseError) as error:
                print(f"[Noticias] {outlet}: feed no disponible ({error}).")
        return found

    def pick(self, already_used: List[str]) -> tuple[Story, Dossier]:
        """La noticia más reciente sin publicar que tenga el texto completo, y su dossier de un solo hecho."""
        now = datetime.datetime.now(datetime.timezone.utc)
        for story in fresh_stories(self.stories(), already_used, now)[:10]:
            try:
                body = article_body(self.http.get(story.url, headers=HEADERS, timeout=20).text)
            except requests.RequestException:
                continue
            if len(body.split()) < MIN_BODY_WORDS:
                continue
            fact = Fact(text=body[:MAX_BODY_CHARS], source_title=f"{story.outlet}: {story.title}", source_url=story.url)
            return story, Dossier(topic=story.title, language="es", facts=[fact], method="noticia")
        raise RuntimeError(f"No hay noticias nuevas con texto completo en las últimas {MAX_AGE_HOURS} h.")
