"""Investigación para documentales con Wikipedia y LLM simulados (sin red)."""

import json
import tempfile
import unittest

from src.core.researcher import Dossier, Fact, Researcher
from src.core.script_generator import Scene

ARTICLE = ("La ciudad fue capturada el 29 de mayo de 1453 tras un asedio de 53 días que había comenzado el 6 de abril. "
           "Mehmed II dirigió un ejército de unos 80.000 hombres contra las murallas teodosianas. "
           "Constantino XI murió durante el asalto final a la ciudad aquel mismo día.")


class _FakeResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class _FakeWiki:
    def get(self, url, params=None, **kwargs):
        if params.get("list") == "search":
            return _FakeResponse({"query": {"search": [{"title": "Caída de Constantinopla"}]}} if "es." in url else {})
        return _FakeResponse({"query": {"pages": {"1": {
            "title": "Caída de Constantinopla", "fullurl": "https://es.wikipedia.org/wiki/Caida", "extract": ARTICLE * 3}}}})


class _FakeLLM:
    def __init__(self, grounded_fails=True, reply=None):
        self.grounded_fails = grounded_fails
        self.reply = reply
        self.prompts = []

    @property
    def has_any(self):
        return self.reply is not None

    def complete_grounded(self, prompt, **kwargs):
        if self.grounded_fails:
            raise RuntimeError("sin cuota")
        return "Dato web", [{"title": "Web", "url": "https://ejemplo.org"}]

    def complete_any(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.reply, "gemini"


class _ExtractLimitedWiki:
    """Como Wikipedia real: con varios títulos en una petición solo uno trae texto completo."""
    def get(self, url, params=None, **kwargs):
        if params.get("list") == "search":
            return _FakeResponse({"query": {"search": [{"title": "Incidente del paso Diátlov"}, {"title": "Do Go On"}]}}
                                 if "es." in url else {})
        titles = params["titles"].split("|")
        return _FakeResponse({"query": {"pages": {t: {   # Wikipedia indexa por id de página, único
            "title": t, "fullurl": f"https://es.wikipedia.org/wiki/{t.replace(' ', '_')}",
            "extract": ARTICLE * 3 if i == len(titles) - 1 else ""} for i, t in enumerate(titles)}}})


class _TwinWiki:
    """Artículo español con enlace a su versión inglesa, que cuenta otra fecha."""
    def __init__(self):
        self.english_searches = 0

    def get(self, url, params=None, **kwargs):
        if params.get("list") == "search":
            self.english_searches += "en." in url
            return _FakeResponse({"query": {"search": [{"title": "USS Cyclops"}]}} if "es." in url else {})
        if params.get("prop") == "langlinks":
            return _FakeResponse({"query": {"pages": {"1": {"langlinks": [{"lang": "en", "*": "USS Cyclops (AC-4)"}]}}}})
        lang = "en" if "en." in url else "es"
        text = ("Lost without a trace in March 1918. " if lang == "en" else "Se hundió en 1919 por un huracán. ") * 20
        return _FakeResponse({"query": {"pages": {lang: {
            "title": params["titles"], "fullurl": f"https://{lang}.wikipedia.org/wiki/Cyclops", "extract": text}}}})


class ResearcherTests(unittest.TestCase):
    def test_fact_with_a_date_the_english_article_contradicts_is_dropped(self):
        from src.core.researcher import _contradicted
        english = "USS Cyclops was lost with 306 people in March 1918. The Bermuda Triangle became famous in 1964."
        self.assertTrue(_contradicted("1919: se hunde la nave carguera USS Cyclops con 308 hombres.", english))
        self.assertFalse(_contradicted("En marzo de 1918 desapareció el USS Cyclops.", english))
        self.assertFalse(_contradicted("El Atlántico es profundo en 1919.", english))   # sin nombre en común: no se juzga

    def test_template_echo_is_not_taken_as_facts(self):
        reply = json.dumps({"facts": [{"text": "...", "source_url": "https://es.wikipedia.org/wiki/Caida"}]})
        dossier = Researcher(_FakeLLM(reply=reply), cache_dir=tempfile.mkdtemp(), session=_FakeWiki()).research("Caída")
        self.assertNotIn("...", [f.text for f in dossier.facts])
        self.assertTrue(dossier.facts)   # se recurre a las frases con datos de Wikipedia

    def test_spanish_article_is_contrasted_with_its_english_version(self):
        wiki, llm = _TwinWiki(), _FakeLLM(reply=json.dumps({"facts": [
            {"text": "Desapareció sin dejar rastro en marzo de 1918.", "source_url": "https://en.wikipedia.org/wiki/Cyclops"}]}))
        dossier = Researcher(llm, cache_dir=tempfile.mkdtemp(), session=wiki).research("El USS Cyclops")
        self.assertEqual(wiki.english_searches, 0)   # se llega por el enlace entre idiomas, no buscando
        self.assertIn("March 1918", llm.prompts[0])
        self.assertIn("1919", llm.prompts[0])
        self.assertIn("CONTRASTE", llm.prompts[0])
        self.assertEqual(dossier.facts[0].source_url, "https://en.wikipedia.org/wiki/Cyclops")

    def test_every_search_hit_arrives_with_its_text(self):
        articles = Researcher(_FakeLLM(), cache_dir=tempfile.mkdtemp(), session=_ExtractLimitedWiki()).wikipedia("Paso Dyatlov")
        self.assertEqual([a["title"] for a in articles], ["Incidente del paso Diátlov", "Do Go On"])

    def test_main_article_is_kept_for_the_archive_photo(self):
        reply = json.dumps({"main_source_url": "https://es.wikipedia.org/wiki/Caida",
                            "facts": [{"text": "Cayó el 29 de mayo de 1453.", "source_url": "https://es.wikipedia.org/wiki/Caida"}]})
        dossier = Researcher(_FakeLLM(reply=reply), cache_dir=tempfile.mkdtemp(), session=_FakeWiki()).research("Caída")
        self.assertEqual(dossier.main_url, "https://es.wikipedia.org/wiki/Caida")

    def test_without_llm_extracts_dated_facts_from_wikipedia(self):
        researcher = Researcher(_FakeLLM(reply=None), cache_dir=tempfile.mkdtemp(), session=_FakeWiki())
        dossier = researcher.research("Caída de Constantinopla")
        self.assertEqual(dossier.method, "wikipedia")
        self.assertTrue(dossier.facts)
        self.assertTrue(all(f.source_url == "https://es.wikipedia.org/wiki/Caida" for f in dossier.facts))
        self.assertIn("1453", dossier.brief())

    def test_llm_condenses_and_keeps_only_allowed_sources(self):
        reply = json.dumps({"facts": [
            {"text": "Cayó el 29 de mayo de 1453.", "source_url": "https://es.wikipedia.org/wiki/Caida"},
            {"text": "Dato de una URL inventada.", "source_url": "https://inventada.com"},
        ], "angles": ["El último emperador"]})
        researcher = Researcher(_FakeLLM(reply=reply), cache_dir=tempfile.mkdtemp(), session=_FakeWiki())
        dossier = researcher.research("Caída de Constantinopla")
        self.assertEqual(dossier.facts[0].source_title, "Caída de Constantinopla")
        self.assertEqual(dossier.facts[1].source_url, "")      # la URL inventada no se acepta como fuente
        self.assertEqual(dossier.source_list(), ["Caída de Constantinopla — https://es.wikipedia.org/wiki/Caida"])

    def test_verification_rewrites_unsupported_scenes(self):
        dossier = Dossier("t", "es", [Fact("Cayó en 1453.", "W", "https://w")])
        reply = json.dumps({"scenes": [
            {"index": 1, "supported": True, "text": "Cayó en 1453."},
            {"index": 2, "supported": False, "text": "El asedio duró 53 días.", "reason": "cifra inventada"},
        ]})
        researcher = Researcher(_FakeLLM(reply=reply), cache_dir=tempfile.mkdtemp(), session=_FakeWiki())
        scenes = [Scene(1, "Cayó en 1453.", "", []), Scene(2, "Murieron un millón de soldados.", "", [])]
        texts, corrections = researcher.verify_script(scenes, dossier)
        self.assertEqual(texts, ["Cayó en 1453.", "El asedio duró 53 días."])
        self.assertEqual(corrections[0]["scene"], 2)

    def test_brief_is_injected_into_director_prompt(self):
        from src.core.director_skills import build_director_prompt
        prompt = build_director_prompt("Tema", {"name": "Historia"}, 90, research_brief="1. Hecho [fuente: W]", platform="tiktok")
        self.assertIn("DOSSIER VERIFICADO", prompt)
        self.assertIn("No afirmes nada que no esté en él", prompt)
        self.assertIn("MÁS de 60 segundos", prompt)


if __name__ == "__main__":
    unittest.main()
