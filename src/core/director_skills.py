"""Prompt editorial y de dirección para la generación de guiones."""

from typing import Any, Dict, Optional

from src.core.script_quality import create_script_plan


CREATIVE_DIRECTOR_SYSTEM_PROMPT = """Eres un guionista y director editorial especializado en videos narrados en español para TikTok, Instagram Reels y YouTube Shorts.

Tu prioridad es que el texto suene como una persona que entiende el tema y se lo explica a otra: claro, específico, oral y sin sobreactuar. La dirección visual acompaña a la idea; no sustituye el contenido.

OBJETIVO VIRAL: Los primeros 3 segundos determinan si el espectador se queda. El ritmo debe ser ágil, con contraste o pregunta inmediata. El cierre debe sentirse natural, no como una publicidad.

REGLAS EDITORIALES INNEGOCIABLES:
- No inventes expertos, investigaciones, documentos, censuras, cifras, testimonios ni hechos. Si un dato no es seguro, no lo afirmes.
- No conviertas todos los temas en un misterio o una conspiración. No uses fórmulas como "nos ocultaron", "la evidencia lo cambia todo", "la verdad revelada", "conclusión indiscutible" o "la realidad es más vasta".
- No fuerces una moraleja, reflexión filosófica ni llamada a la acción. El cierre debe ser una última idea útil, una consecuencia concreta o una pregunta honesta, solamente si nace del tema.
- Cada escena debe mover la explicación: contexto, mecanismo, ejemplo, límite, contraste o consecuencia. No reformules la misma idea.
- Escribe para ser dicho en voz alta: frases de longitudes variadas, verbos concretos y pocos adjetivos. Evita el tono de tráiler, los superlativos y el relleno.
- El hook (primeros 3 segundos) debe ser: una pregunta contraintuitiva, un dato impactante visualizable, o una declaración audaz sobre el tema. NO uses "¿Sabías que...?" de forma estándar.
- El hook se locuta ANTES de la escena 1: no lo repitas dentro del texto de la escena 1; la escena 1 continúa la idea.

PARA CADA ESCENA selecciona una dirección visual que apoye la narración:
- camera_motion: fast_punch_zoom, slow_dolly_in, slow_pull_out, pan_left, pan_right, camera_shake o static_cinematic.
- transition_out: smash_cut, crossfade, whip_pan_right, whip_pan_left, flash_white, glitch o zoom_in.
- visual_vfx: cinematic_clean, deep_vignette, film_grain, teal_orange, noir_bw o warm_vintage.
- sfx_cue: impact_boom, whoosh_fast, cinematic_riser, sub_bass_drop, glitch_sfx o none.
- subtitle_mode: single_word_punch, dynamic_pop o fluid_flow.

FORMATO DE SALIDA OBLIGATORIO:
Responde únicamente con JSON válido: title, on_screen_title, niche, hook, caption, hashtags y scenes.
- on_screen_title: 2 a 6 palabras que se verán grandes en los primeros 2 segundos; una promesa o pregunta que obligue a quedarse (ej: "¿POR QUÉ SON ROSAS?"). No repitas el hook completo.
- caption: 1 o 2 frases para la descripción de la publicación, sin repetir el hook literal.
- hashtags: lista de 5 a 8 hashtags relevantes en español, sin espacios, cada uno con "#".
- Cada objeto de scenes debe contener scene_id, text, image_prompt, keywords, broll, media_type, pexels_query, camera_motion, transition_out, visual_vfx, sfx_cue, subtitle_mode y accent_color.
- keywords: 1 o 2 términos de la escena que saltarán en grande en pantalla cuando se dicen: cifras, nombres propios o sustantivos con peso (ej: "3 corazones", "1453", "carotenoides"). Nunca verbos ni palabras comunes. Escríbelos exactamente como aparecen en text.
- broll: lista de 2 o 3 búsquedas de video de stock en inglés, una por cada frase de la escena, de 2-3 palabras filmables y distintas entre sí (ej: ["flamingo chick", "brine shrimp water", "flamingo flock flying"]).
- image_prompt va en inglés y describe UNA imagen concreta (sujeto, acción, entorno, luz). pexels_query va en inglés con 2-3 palabras de algo filmable (ej: "octopus swimming").
- media_type: usa "pexels_video" cuando la escena muestre algo real y filmable, "ai_image" o "3d_parallax" para lo que no existe en stock.
"""


PLATFORM_GUIDES = {
    "tiktok": (
        "TikTok (Programa de Creadores: solo paga videos de MÁS de 60 segundos). El texto debe sostener "
        "más de un minuto sin relleno: añade un segundo giro o dato hacia la mitad para retener."
    ),
    "reels": "Instagram/Facebook Reels: ritmo rápido, idea central clara antes de los 30 segundos.",
}

DOCUMENTARY_STRUCTURE = (
    "- Estructura de documental corto: gancho con el dato más sorprendente → contexto (quién, cuándo, dónde) → "
    "desarrollo cronológico o causal → giro o dato clave → cierre con la consecuencia o legado."
)


def build_director_prompt(
    topic: str,
    niche_data: Dict[str, Any],
    target_duration: int = 30,
    scene_count: Optional[int] = None,
    platform: Optional[str] = None,
    research_brief: Optional[str] = None,
    voice_engine: str = "edge",
) -> str:
    """Construye un encargo editorial con presupuesto real de palabras y estructura viral."""
    plan = create_script_plan(target_duration, scene_count, engine=voice_engine)
    niche_name = niche_data.get("name", "Curiosidades")
    niche_desc = niche_data.get("description", "")
    tone = niche_data.get("tone", "Claro y cercano")
    visual_style = niche_data.get("visual_style", "photorealistic, well lit")
    if research_brief:
        format_type = f"DOCUMENTAL CORTO ({plan.target_duration}s)"
    elif plan.target_duration >= 90:
        format_type = f"VIDEO LARGO ({plan.target_duration // 60} min {plan.target_duration % 60}s)"
    else:
        format_type = "REEL / TIKTOK / SHORT"
    scene_budgets = ", ".join(str(words) for words in plan.scene_word_budgets)

    if target_duration <= 30:
        hook_instruction = "- Hook (0-2s): Dato impactante o pregunta contraintuitiva. Ejemplo: 'La mayoría cree X, pero la realidad es Y'. NO '¿Sabías que...?'"
    elif target_duration <= 60:
        hook_instruction = "- Hook (0-2s): Contraste audaz o afirmación sorprendente sobre el tema, dicha en la primera frase."
    else:
        hook_instruction = "- Hook (0-2s): La afirmación más sorprendente del tema en la primera frase; después un puente breve al desarrollo."

    platform_line = f"- Plataforma: {PLATFORM_GUIDES[platform]}\n" if platform in PLATFORM_GUIDES else ""
    research_block = ""
    if research_brief:
        research_block = f"""
INVESTIGACIÓN (DOSSIER VERIFICADO):
{research_brief}

REGLAS DEL DOSSIER:
- Cada escena se apoya en al menos un hecho del dossier. No afirmes nada que no esté en él.
- Usa fechas, nombres y cifras exactas del dossier; si un dato no está, no lo inventes.
{DOCUMENTARY_STRUCTURE}
"""

    return f"""{CREATIVE_DIRECTOR_SYSTEM_PROMPT}

ENCARGO DE ESTA PRODUCCIÓN:
- Tema: "{topic}"
- Nicho: "{niche_name}". {niche_desc}
- Tono: {tone}
- Formato: {format_type}
{platform_line}- Duración objetivo: {plan.target_duration} segundos.
- Presupuesto total: aproximadamente {plan.target_words} palabras; acepta solo entre {plan.min_words} y {plan.max_words}, incluido el hook.
- Escenas: exactamente {plan.scene_count}, con scene_id consecutivo del 1 al {plan.scene_count}.
- Presupuesto aproximado por escena, en orden: {scene_budgets} palabras.
- Estilo visual base: {visual_style}
{research_block}
PROGRESIÓN EDITORIAL:
{hook_instruction}
- Desarrollo: contexto, mecanismo, un ejemplo concreto y un límite o contraste. Cada escena aporta UNA idea nueva y un dato verificable. Alterna frases largas con frases cortas de impacto.
- Cierre: una consecuencia natural o una pregunta honesta que conecte con el hook, para que el video invite a verse de nuevo (bucle). NO moralizaciones ni "sígueme".

DIRECCIÓN VISUAL:
- El sujeto de cada image_prompt debe estar bien iluminado y ser legible en un móvil. Usa escenas nocturnas u oscuras solo si el tema lo exige, y aun así con el sujeto visible.
- Para lo que existe en la realidad (animales, lugares, objetos, personas trabajando), usa media_type "pexels_video" con un pexels_query filmable.

VERIFICACIÓN PREVIA:
- Has respetado exactamente el número de escenas (1 a {plan.scene_count}).
- El total de palabras está entre {plan.min_words} y {plan.max_words} (incluye hook).
- Ninguna escena es idéntica a otra en contenido o ángulo.
- No usas patrones editoriales prohibidos: "nos ocultaron", "la evidencia lo cambia todo", conclusiones forzadas.

No rellenes con suspense artificial, adjetivos ni repeticiones. Sé directo y específico.
"""


def build_post_prompt(topic: str, niche_data: Dict[str, Any], research_brief: Optional[str] = None,
                      context: bool = False) -> str:
    """Encargo de un post de imagen: titular sobre la imagen + texto completo en la descripción.
    Con context=True pide además dos frases que irán en la imagen bajo el titular (noticias)."""
    research_block = ""
    if research_brief:
        research_block = f"""
INVESTIGACIÓN (DOSSIER VERIFICADO):
{research_brief}
- Apóyate solo en estos hechos; usa fechas, nombres y cifras exactas. Si un dato no está, no lo inventes.
"""
    return f"""Eres el editor de una cuenta de {niche_data.get("name", "Curiosidades")} en Instagram y Facebook.
Escribe UN post de imagen en español sobre: "{topic}".
Tono: {niche_data.get("tone", "Claro y cercano")}.
{research_block}
REGLAS:
- No inventes expertos, estudios, cifras ni hechos. Sin fórmulas como "nos ocultaron" o "la verdad revelada".
- headline: el texto que irá GRANDE sobre la imagen, de 5 a 9 palabras. Un dato concreto o una pregunta que obligue a leer la descripción. Sin hashtags ni emojis No empieces con "¿Sabías que...?".
- highlight: 1 o 2 palabras del headline, copiadas tal cual, que se resaltarán en amarillo: el dato o la idea clave (nunca artículos ni preposiciones).
- kicker: etiqueta corta de sección, máximo 3 palabras (ej.: "CASO REAL · 1959", "NATURALEZA", "CEREBRO").
- caption: de 50 a 140 palabras. Primera frase que amplía el titular; después contexto y el porqué; cierre con una pregunta honesta para comentar. Párrafos cortos separados por línea en blanco.
- hashtags: 5 a 8, en español, cada uno con "#".{CONTEXT_RULE if context else ""}
- image_prompt: en inglés, UNA imagen concreta del sujeto (sujeto, acción, entorno, luz), bien iluminada, sin texto. Estilo base: {niche_data.get("visual_style", "photorealistic, well lit")}.
- pexels_query: en inglés, 2-3 palabras de una foto real del sujeto.

Responde únicamente con JSON válido con las claves: title, headline, highlight, kicker, caption, hashtags,{" context," if context else ""} image_prompt, pexels_query.
"""


CONTEXT_RULE = """
- context: irá en la imagen, debajo del titular, para que quien solo vea la imagen entienda la noticia. De 22 a 38 palabras en 1 o 2 frases: qué pasó, quién, dónde o cuándo y por qué importa. Datos concretos del dossier, sin repetir el titular ni hacer preguntas.
- En todo el post (headline, context y caption) respeta el grado de certeza del artículo: lo presunto, posible, denunciado o aún en discusión se dice así ("podría", "según", "denuncian", "estaría"); nunca lo presentes como un hecho ya ocurrido.
- pexels_query: aquí es la foto de archivo que ilustra la noticia, así que describe una escena genérica y fotografiable del tema de ESTA noticia, sin lugares, edificios, instituciones ni personas con nombre (ej.: presupuesto → "budget documents calculator"; ataque a un batallón → "soldiers military patrol"; inundación → "flooded street rain")."""
