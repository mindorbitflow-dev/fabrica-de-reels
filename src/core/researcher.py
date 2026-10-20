"""Investigación para documentales cortos.

1. Wikipedia (API pública, es + en): texto de los artículos más relevantes, con URL.
2. Gemini con búsqueda de Google (si hay GEMINI_API_KEY y cuota): datos adicionales con fuentes.
3. El LLM condensa todo en un dossier de hechos verificables, cada uno con su fuente.
Después, verify_script() revisa el guion contra el dossier y reescribe lo que no tenga respaldo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.core.job_manager import write_json_atomic

WIKI_HEADERS = {"User-Agent": "AIReelFactory/2.0 (investigación de guiones; uso personal)"}
WIKI_TIMEOUT = 20
MAX_EXTRACT_CHARS = 12000
# Con menos hechos el dossier no sirve para juzgar el guion: todo parece "no respaldado" y el
# verificador reescribiría cada escena con el mismo dato (pasó con Cicada 3301 y un solo hecho).
MIN_FACTS_TO_VERIFY = 4


@dataclass
class Fact:
    text: str
    source_title: str = ""
    source_url: str = ""


@dataclass
class Dossier:
    topic: str
    language: str
    facts: List[Fact] = field(default_factory=list)
    angles: List[str] = field(default_factory=list)
    sources: List[Dict[str, str]] = field(default_factory=list)
    method: str = ""          # "wikipedia+busqueda", "wikipedia", "sin_fuentes"
    main_url: str = ""        # artículo que trata del tema (de ahí sale la foto de archivo)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dossier":
        return cls(
            topic=data.get("topic", ""), language=data.get("language", "es"),
            facts=[Fact(**f) for f in data.get("facts", [])], angles=list(data.get("angles", [])),
            sources=list(data.get("sources", [])), method=data.get("method", ""),
            main_url=data.get("main_url", ""),
        )

    def brief(self) -> str:
        """Texto para el prompt del guionista: hechos numerados con su fuente."""
        lines = [f"{i + 1}. {fact.text} [fuente: {fact.source_title or fact.source_url}]" for i, fact in enumerate(self.facts)]
        if self.angles:
            lines.append("Ángulos narrativos posibles: " + " | ".join(self.angles))
        return "\n".join(lines)

    def source_list(self) -> List[str]:
        seen, result = set(), []
        for fact in self.facts:
            if fact.source_url and fact.source_url not in seen:
                seen.add(fact.source_url)
                result.append(f"{fact.source_title} — {fact.source_url}".strip(" —"))
        return result


class Researcher:
    def __init__(self, llm, cache_dir: str = "output/research_cache", session: Optional[requests.Session] = None):
        self.llm = llm
        self.cache_dir = cache_dir
        self.http = session or requests.Session()

    # ------------------------------------------------------------------ Wikipedia
    def wikipedia(self, query: str, lang: str = "es", limit: int = 3) -> List[Dict[str, str]]:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        try:
            search = self.http.get(api, params={
                "action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json",
            }, headers=WIKI_HEADERS, timeout=WIKI_TIMEOUT).json()
            titles = [hit["title"] for hit in search.get("query", {}).get("search", [])]
            return self._articles(lang, titles)
        except (requests.RequestException, ValueError) as error:
            print(f"[Researcher] Wikipedia ({lang}) no respondió: {type(error).__name__}")
            return []

    def counterpart(self, article: Dict[str, str], lang: str) -> Optional[Dict[str, str]]:
        """El mismo artículo en otro idioma, por el enlace entre idiomas de Wikipedia (buscar el título
        traducido falla: «Paso Dyatlov» no da resultados en la Wikipedia inglesa)."""
        source_lang = article["url"].split("//")[1].split(".")[0]
        try:
            pages = self.http.get(f"https://{source_lang}.wikipedia.org/w/api.php", params={
                "action": "query", "prop": "langlinks", "lllang": lang, "titles": article["title"], "format": "json",
            }, headers=WIKI_HEADERS, timeout=WIKI_TIMEOUT).json().get("query", {}).get("pages", {}) or {}
            title = next((link.get("*") for page in pages.values() for link in page.get("langlinks", [])), None)
            found = self._articles(lang, [title]) if title else []
        except (requests.RequestException, ValueError) as error:
            print(f"[Researcher] Wikipedia ({lang}) no respondió: {type(error).__name__}")
            return None
        return found[0] if found else None

    def _articles(self, lang: str, titles: List[str]) -> List[Dict[str, str]]:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        # Un artículo por petición: con varios títulos, TextExtracts solo devuelve el texto completo
        # de uno y deja vacíos los demás (exlimit=1), y a veces el que llega es el menos relevante.
        pages = {}
        for title in titles:
            pages.update(self.http.get(api, params={
                "action": "query", "prop": "extracts|info", "explaintext": 1, "inprop": "url",
                "titles": title, "format": "json",
            }, headers=WIKI_HEADERS, timeout=WIKI_TIMEOUT).json().get("query", {}).get("pages", {}) or {})
        articles = []
        for page in pages.values():
            extract = (page.get("extract") or "").strip()
            if len(extract) > 200:
                articles.append({
                    "title": page.get("title", ""),
                    "url": page.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                    "text": extract,   # completo para el contraste; a la IA se le pasa recortado
                })
        order = {t: i for i, t in enumerate(titles)}
        return sorted(articles, key=lambda a: order.get(a["title"], 99))

    # ------------------------------------------------------------------ dossier
    def research(self, topic: str, language: str = "es", fact_count: int = 12) -> Dossier:
        # "v2": los dossieres anteriores no contrastaban con la otra Wikipedia y no deben reutilizarse.
        cache_path = os.path.join(self.cache_dir, hashlib.sha1(f"v2|{language}|{topic}".encode("utf-8")).hexdigest()[:16] + ".json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as file:
                    return Dossier.from_dict(json.load(file))
            except (OSError, json.JSONDecodeError):
                pass

        other = "en" if language == "es" else "es"
        articles = self.wikipedia(topic, language, 3)
        # Contraste: el artículo principal en el otro idioma. Si no existe, se busca el tema allí.
        twin = self.counterpart(articles[0], other) if articles else None
        articles += [twin] if twin else self.wikipedia(topic, other, 2)
        sources = [{"title": a["title"], "url": a["url"]} for a in articles]

        grounded_text = ""
        try:
            grounded_text, web_sources = self.llm.complete_grounded(
                f"Investiga el tema \"{topic}\" para un documental corto. Enumera hechos concretos y verificables "
                "(fechas, lugares, nombres, cifras) y aclara qué está confirmado y qué es especulación. Responde en español."
            )
            sources += [s for s in web_sources if s.get("url")]
        except Exception as error:
            print(f"[Researcher] Búsqueda web no disponible ({str(error)[:120]}); se usa solo Wikipedia.")

        if not articles and not grounded_text:
            dossier = Dossier(topic, language, method="sin_fuentes")
        else:
            dossier = None
            if self.llm.has_any:
                try:
                    dossier = self._condense(topic, language, articles, grounded_text, sources, fact_count)
                except Exception as error:
                    print(f"[Researcher] No se pudo condensar con IA ({str(error)[:120]}); extraigo datos de Wikipedia.")
            if dossier is None or not dossier.facts:
                dossier = self._extract_without_llm(topic, language, articles, fact_count)
            # Contraste que no depende de la IA (un modelo de respaldo puede ignorar la regla del prompt).
            twin_text = "\n".join(a["text"] for a in articles if f"//{other}." in a["url"])
            kept = [f for f in dossier.facts if not _contradicted(f.text, twin_text)]
            for fact in dossier.facts:
                if fact not in kept:
                    print(f"[Researcher] Dato descartado: la Wikipedia en '{other}' da otra fecha: {fact.text[:100]}")
            dossier.facts = kept
        dossier.sources = sources
        if dossier.facts:
            write_json_atomic(cache_path, dossier.to_dict())
        return dossier

    def _condense(self, topic, language, articles, grounded_text, sources, fact_count) -> Dossier:
        material = "\n\n".join(f"### {a['title']} ({a['url']})\n{a['text'][:MAX_EXTRACT_CHARS]}" for a in articles)
        if grounded_text:
            material += f"\n\n### Búsqueda web\n{grounded_text[:MAX_EXTRACT_CHARS]}"
        allowed_urls = [s["url"] for s in sources]
        prompt = (
            f"Eres documentalista. Con SOLO el material siguiente, extrae {fact_count} hechos verificables y "
            f"atractivos sobre \"{topic}\" para un documental corto en {'español' if language == 'es' else 'inglés'}. "
            "Primero decide qué artículo trata DE ESE TEMA: los nombres pueden estar escritos con otra grafía o "
            "transliteración (Dyatlov = Diátlov). Toma los hechos de ese artículo e ignora los que solo lo mencionan "
            "de pasada (listas, pódcast, obras de ficción).\n"
            "CONTRASTE: si el mismo artículo aparece en español y en inglés y no coinciden en una fecha, cifra, "
            "nombre o causa, NO incluyas ese dato. Incluye solo lo que ambas versiones respaldan o lo que solo "
            "una menciona sin que la otra lo contradiga.\n"
            "Cada hecho: una frase con datos concretos (fecha, lugar, nombre o cifra). No incluyas nada que no "
            "esté en el material. Marca como especulación lo que el material presente como teoría.\n"
            f"URLs permitidas como fuente: {json.dumps(allowed_urls, ensure_ascii=False)}\n"
            'Responde solo JSON: {"main_source_url": "URL del artículo que trata del tema", '
            '"facts": [{"text": "...", "source_url": "una URL permitida"}], '
            '"angles": ["ángulo narrativo 1", "ángulo 2"]}\n\nMATERIAL:\n' + material
        )
        raw, _ = self.llm.complete_any(prompt, max_tokens=3000, temperature=0.2)
        data = _load_json(raw)
        titles = {s["url"]: s.get("title", "") for s in sources}
        facts = []
        for item in (data.get("facts") or [])[:fact_count]:
            text = str(item.get("text", "")).strip()
            url = str(item.get("source_url", "")).strip()
            # Un modelo débil a veces copia la plantilla ("...") en lugar de rellenarla: no es un hecho.
            if len(re.findall(r"\w+", text)) >= 4:
                facts.append(Fact(text, titles.get(url, ""), url if url in titles else ""))
        method = "wikipedia+busqueda" if grounded_text else "wikipedia"
        main_url = str(data.get("main_source_url", "")).strip()
        return Dossier(topic, language, facts, [str(a) for a in data.get("angles", [])][:3], method=method,
                       main_url=main_url if main_url in titles else "")

    @staticmethod
    def _extract_without_llm(topic, language, articles, fact_count) -> Dossier:
        """Sin LLM: frases de Wikipedia que contienen datos concretos (números, fechas)."""
        facts = []
        for article in articles:
            if article["url"].split("//")[1].split(".")[0] != language:
                continue
            # Corta solo antes de una mayúscula: evita partir en abreviaturas como "a. C.".
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", article["text"]):
                sentence = " ".join(sentence.split())
                if (60 <= len(sentence) <= 260 and re.search(r"\d", sentence) and "==" not in sentence
                        and sentence[0].isupper()):
                    facts.append(Fact(sentence, article["title"], article["url"]))
                if len(facts) >= fact_count:
                    break
        # Sin IA, el primer resultado del buscador de Wikipedia es el más probable.
        main_url = next((a["url"] for a in articles if a["url"].split("//")[1].split(".")[0] == language), "")
        return Dossier(topic, language, facts, method="wikipedia", main_url=main_url)

    # ------------------------------------------------------------------ verificación
    def verify_script(self, scenes: List[Any], dossier: Dossier) -> Tuple[List[str], List[Dict[str, str]]]:
        """Devuelve (textos corregidos por escena, correcciones). Sin LLM, no cambia nada."""
        texts = [s.text for s in scenes]
        if len(dossier.facts) < MIN_FACTS_TO_VERIFY or not self.llm.has_any:
            return texts, []
        prompt = (
            "Compara cada escena del guion con el dossier de hechos. Si una escena afirma algo que el dossier no "
            "respalda (fechas, cifras, nombres o afirmaciones), reescríbela usando solo hechos del dossier, con una "
            "longitud parecida y el mismo tono. Si está respaldada, déjala igual. Cada escena debe contar algo "
            "distinto: nunca repitas en una escena lo que ya dice otra.\n\n"
            f"DOSSIER:\n{dossier.brief()}\n\nESCENAS:\n"
            + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
            + '\n\nResponde solo JSON: {"scenes": [{"index": 1, "supported": true, "text": "texto final", "reason": ""}]}'
        )
        try:
            raw, _ = self.llm.complete_any(prompt, max_tokens=4000, temperature=0.2)
        except Exception as error:
            print(f"[Researcher] No se pudo verificar el guion: {error}")
            return texts, []
        corrections = []
        for item in _load_json(raw).get("scenes", []):
            try:
                index = int(item.get("index", 0)) - 1
            except (TypeError, ValueError):
                continue
            new_text = str(item.get("text", "")).strip()
            # Una reescritura que copia otra escena dejaría el guion repitiendo la misma frase.
            if new_text in texts:
                continue
            if 0 <= index < len(texts) and not item.get("supported", True) and new_text:
                corrections.append({"scene": index + 1, "before": texts[index], "after": new_text, "reason": str(item.get("reason", ""))})
                texts[index] = new_text
        return texts, corrections


_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def _contradicted(fact: str, other_text: str) -> bool:
    """True si el otro artículo habla de los mismos nombres propios con años distintos a los del hecho
    (p. ej. «1919: se hunde el USS Cyclops» frente a «Cyclops ... lost in March 1918»)."""
    years = set(_YEAR.findall(fact))
    # Nombres propios: palabras con mayúscula que no abren el texto (esa mayúscula es solo ortográfica).
    names = {m.group(0) for m in re.finditer(r"\b[A-ZÁÉÍÓÚÑ][\wáéíóúñ]{3,}", fact) if m.start() > 0}
    if not years or not names or not other_text:
        return False
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", other_text) if any(re.search(rf"\b{re.escape(n)}\b", s) for n in names)]
    other_years = {y for s in sentences for y in _YEAR.findall(s)}
    return bool(other_years) and not (years & other_years)


def _load_json(raw: str) -> Dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw or "", flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(re.sub(r",\s*([\]}])", r"\1", cleaned[start:end + 1]))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
