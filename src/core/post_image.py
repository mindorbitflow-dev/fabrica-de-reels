"""Composición del post de imagen: titular sobre la foto, con etiqueta de sección y firma.

Tres estilos (el nicho elige cuál en config.yaml → posts.styles):
- editorial: frase en minúsculas, palabras clave en amarillo, etiqueta arriba y firma abajo.
- cajas: mayúsculas sobre cajas negras y las palabras clave sobre caja amarilla.
- noticia: como editorial, con dos frases de contexto bajo el titular para entender la noticia sin abrir el texto.
"""

import os
from typing import Iterable, List, Tuple

from PIL import Image, ImageDraw, ImageFont

POST_SIZE = (1080, 1350)   # 4:5, el formato vertical que Instagram muestra sin recortar en el feed
MARGIN = 72
ACCENT = (255, 212, 0)
FONTS_DIR = "assets/fonts"   # Montserrat (OFL), la misma fuente de los subtítulos de los reels

Word = Tuple[str, bool, float]   # texto, resaltada, ancho en píxeles


def _font(weight: str, size: int):
    return ImageFont.truetype(os.path.join(FONTS_DIR, f"Montserrat-{weight}.ttf"), size)


def _norm(word: str) -> str:
    return word.lower().strip(".,;:¿?¡!\"'()«»")


def _wrap(draw: ImageDraw.ImageDraw, headline: str, highlight: Iterable[str], font, max_width: float,
          gap: float, upper: bool) -> List[List[Word]]:
    marked = {_norm(w) for phrase in highlight for w in phrase.split()}
    lines: List[List[Word]] = [[]]
    width = 0.0
    for raw in headline.split():
        text = raw.upper() if upper else raw
        w = draw.textlength(text, font=font)
        if lines[-1] and width + gap + w > max_width:
            lines.append([])
            width = 0.0
        width += (gap if lines[-1] else 0) + w
        lines[-1].append((text, _norm(raw) in marked, w))
    return [line for line in lines if line]


def _fit(draw, headline, highlight, weight, sizes, max_lines, gap_ratio, upper, max_width):
    """El mayor tamaño de letra con el que el titular cabe en max_lines líneas."""
    for size in sizes:
        font = _font(weight, size)
        lines = _wrap(draw, headline, highlight, font, max_width, size * gap_ratio, upper)
        if len(lines) <= max_lines:
            break
    return font, size, lines


def _shade(image: Image.Image, start: float, strength: int) -> None:
    width, height = image.size
    column = Image.new("L", (1, height))
    for y in range(height):
        column.putpixel((0, y), int(strength * max(0.0, (y - height * start) / (height * (1 - start))) ** 1.2))
    image.paste(Image.new("RGB", image.size, (0, 0, 0)), (0, 0), column.resize(image.size))


def _pill(draw, x: float, y: float, text: str, fill, color) -> None:
    font = _font("ExtraBold", 26)
    width = draw.textlength(text, font=font)
    draw.rounded_rectangle((x, y, x + width + 40, y + 52), radius=26, fill=fill)
    draw.text((x + 20, y + 26), text, font=font, fill=color, anchor="lm")


def _editorial(image, draw, headline, highlight, kicker, handle, context="") -> None:
    width, height = image.size
    _shade(image, 0.38, 235)
    if kicker:
        _pill(draw, MARGIN, MARGIN, kicker.upper(), ACCENT, "black")
    font, size, lines = _fit(draw, headline, highlight, "ExtraBold", range(96, 51, -4), 4, 0.28, False, width - 2 * MARGIN)
    line_height = int(size * 1.1)
    y = height - 150 - line_height * len(lines)
    for line in lines:
        x = MARGIN
        for text, marked, w in line:
            draw.text((x, y), text, font=font, fill=ACCENT if marked else "white")
            x += w + size * 0.28
        y += line_height
    if handle:
        draw.rectangle((MARGIN, height - 108, MARGIN + 64, height - 102), fill=ACCENT)
        draw.text((MARGIN + 84, height - 105), handle, font=_font("SemiBold", 28), fill=(225, 225, 225), anchor="lm")


def _cajas(image, draw, headline, highlight, kicker, handle, context="") -> None:
    width, height = image.size
    _shade(image, 0.55, 150)
    pad, gap = 14, 10   # relleno de cada caja y separación entre cajas
    font, size, lines = _fit(draw, headline, highlight, "Black", range(78, 45, -4), 5, 0, True,
                             width - 2 * MARGIN - 2 * pad)
    # Con el tamaño elegido se reparte de nuevo contando relleno y separación de las cajas.
    lines = _wrap(draw, headline, highlight, font, width - 2 * MARGIN - 2 * pad, 2 * pad + gap, True)
    box_height = int(size * 1.22)
    line_step = box_height + 8
    y = height - 150 - line_step * len(lines)
    if kicker:
        _pill(draw, MARGIN, y - 76, kicker.upper(), "white", "black")
    for line in lines:
        x = MARGIN
        for text, marked, w in line:
            draw.rectangle((x, y, x + w + 2 * pad, y + box_height), fill=ACCENT if marked else (12, 12, 12))
            draw.text((x + pad, y + box_height / 2), text, font=font, fill="black" if marked else "white", anchor="lm")
            x += w + 2 * pad + gap
        y += line_step
    if handle:
        draw.text((width / 2, height - 80), handle, font=_font("SemiBold", 28), fill="white", anchor="mm")


def _noticia(image, draw, headline, highlight, kicker, handle, context="") -> None:
    width, height = image.size
    _shade(image, 0.22, 245)
    if kicker:
        _pill(draw, MARGIN, MARGIN, kicker.upper(), ACCENT, "black")
    max_width = width - 2 * MARGIN
    body_font = _font("SemiBold", 34)
    body = [" ".join(t for t, _, _ in line)
            for line in _wrap(draw, context, (), body_font, max_width, draw.textlength(" ", font=body_font), False)][:6]
    body_step, rule_gap = 46, 34   # interlineado del contexto y espacio de la raya que lo separa del titular
    font, size, lines = _fit(draw, headline, highlight, "ExtraBold", range(76, 47, -4), 3, 0.28, False, max_width)
    line_height = int(size * 1.1)
    y = height - 140 - body_step * len(body) - (2 * rule_gap if body else 0) - line_height * len(lines)
    for line in lines:
        x = MARGIN
        for text, marked, w in line:
            draw.text((x, y), text, font=font, fill=ACCENT if marked else "white")
            x += w + size * 0.28
        y += line_height
    if body:
        y += rule_gap - 8
        draw.rectangle((MARGIN, y, MARGIN + 64, y + 6), fill=ACCENT)
        y += rule_gap
        for line in body:
            draw.text((MARGIN, y), line, font=body_font, fill=(235, 235, 235))
            y += body_step
    if handle:
        draw.text((MARGIN, height - 82), handle, font=_font("SemiBold", 28), fill=(200, 200, 200), anchor="lm")


STYLES = {"editorial": _editorial, "cajas": _cajas, "noticia": _noticia}


def render_post(image_path: str, headline: str, out_path: str, style: str = "editorial",
                highlight: Iterable[str] = (), kicker: str = "", handle: str = "", context: str = "") -> str:
    """Recorta la foto a 4:5 y compone el titular con el estilo pedido. Guarda JPEG (Instagram no acepta otro)."""
    width, height = POST_SIZE
    base = Image.open(image_path).convert("RGB")
    scale = max(width / base.width, height / base.height)
    base = base.resize((round(base.width * scale), round(base.height * scale)), Image.LANCZOS)
    left, top = (base.width - width) // 2, (base.height - height) // 2
    base = base.crop((left, top, left + width, top + height))
    STYLES.get(style, _editorial)(base, ImageDraw.Draw(base), headline, list(highlight), kicker, handle, context)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    base.save(out_path, "JPEG", quality=92)
    return out_path
