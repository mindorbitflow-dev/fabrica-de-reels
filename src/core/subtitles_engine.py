"""
Subtitles Engine Module
Genera subtítulos animados dinámicos estilo viral (TikTok / Alex Hormozi) en formato ASS
con resaltado palabra por palabra en tiempo real.
"""

import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from src.core.tts_engine import WordTiming

# Color de resaltado (ASS BGR) por preset de la UI. "director" respeta el color de cada escena.
STYLE_PRESETS = {
    "yellow_pop": "&H0000E5FF&",
    "green_neon": "&H003BF029&",
    "cyan_electric": "&H00FFFF00&",
    "red_fire": "&H003333FF&",
    "white_pure": "&H00FFFFFF&",
}


def escape_ass_text(text: str) -> str:
    """Evita que llaves o barras del texto se interpreten como etiquetas ASS."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def format_ass_time(seconds: float) -> str:
    """Convierte segundos a formato de tiempo ASS (H:MM:SS.cs)"""
    total_cs = max(0, int(round(seconds * 100)))
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


class SubtitlesEngine:
    def __init__(
        self,
        font_name: str = "Arial",
        font_size: int = 70,
        margin_v: int = 380,
        words_per_line: int = 3,
        active_color: str = "&H0000E5FF&",    # Amarillo BGR (&H00BBGGRR&)
        inactive_color: str = "&H00FFFFFF&",  # Blanco
        outline_color: str = "&H00000000&",   # Negro
        outline_width: int = 7,
        shadow_width: int = 3,
        uppercase: bool = True
    ):
        self.font_name = font_name
        self.font_size = font_size
        self.margin_v = margin_v
        self.words_per_line = words_per_line
        self.active_color = active_color
        self.inactive_color = inactive_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.shadow_width = shadow_width
        self.uppercase = uppercase

    def generate_directed_ass(
        self,
        words: List[WordTiming],
        output_ass_path: str,
        scenes: List[Any],
        scene_durations: List[float],
        width: int = 1080,
        height: int = 1920,
        font_size: Optional[int] = None,
        position: str = "bottom",
        style_preset: Optional[str] = None,
        words_per_line: Optional[int] = None,
        uppercase: Optional[bool] = None,
        title_card: Optional[str] = None,
        pop_words: Optional[List[Any]] = None,
    ) -> str:
        """
        Genera subtítulos kinéticos virales adaptando dinámicamente el ritmo, tamaño y color
        de acuerdo a las directivas creativas del Director de IA para cada escena.
        Aplica efecto Bouncy Pop (\fscx) y zonas seguras anti-bloqueo para TikTok/Reels.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)

        # Márgenes seguros de TikTok/Reels: margen derecho amplio para no tapar con botones de like/comentario
        margin_l = 80
        margin_r = 140 if width < height else 60

        if width > height:  # 16:9 Horizontal
            default_fs = 54
            margin_v = int(height * 0.14)
        else:               # 9:16 / 1:1
            default_fs = 72 if width < height else 64
            bottom_margin = self.margin_v if width < height else int(height * 0.12)
            margin_v = {"bottom": bottom_margin, "center": int(height * 0.44), "top": int(height * 0.78)}.get(position, bottom_margin)

        f_size = font_size or default_fs
        preset_accent = STYLE_PRESETS.get(style_preset or "")
        use_upper = self.uppercase if uppercase is None else uppercase
        max_per_line = max(1, int(words_per_line)) if words_per_line else None

        # Mapear cada palabra a su escena correspondiente según timestamps
        scene_boundaries = []
        accum = 0.0
        for d in scene_durations:
            scene_boundaries.append((accum, accum + d))
            accum += d

        # Header ASS profesional con sombras profundas y anti-aliasing
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{self.font_name},{f_size},{self.inactive_color},&H000000FF&,{self.outline_color},&H90000000&,-1,0,0,0,100,100,1,0,1,{self.outline_width},{self.shadow_width},2,{margin_l},{margin_r},{margin_v},1
Style: Punch,{self.font_name},{int(f_size * 1.25)},{self.inactive_color},&H000000FF&,{self.outline_color},&H90000000&,-1,0,0,0,100,100,2,0,1,{self.outline_width + 1},{self.shadow_width + 1},2,{margin_l},{margin_r},{margin_v},1
Style: Title,{self.font_name},{int(f_size * 1.05)},&H00FFFFFF&,&H000000FF&,&H00000000&,&HB0000000&,-1,0,0,0,100,100,1,0,3,14,0,8,{margin_l},{margin_r},{int(height * 0.13)},1
Style: Pop,{self.font_name},{int(f_size * 1.7)},{preset_accent or "&H0000E5FF&"},&H000000FF&,{self.outline_color},&H90000000&,-1,0,0,0,100,100,2,0,1,{self.outline_width + 3},{self.shadow_width + 2},8,{margin_l},{margin_r},{int(height * 0.30)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        events = []
        word_idx = 0
        total_words = len(words)

        for s_idx, scene in enumerate(scenes):
            s_start, s_end = scene_boundaries[s_idx] if s_idx < len(scene_boundaries) else (0, 9999)
            sub_mode = getattr(scene, "subtitle_mode", "dynamic_pop")
            accent_col = preset_accent or getattr(scene, "accent_color", "&H0000E5FF&")

            # Determinar agrupación según la orden de la IA
            if sub_mode == "single_word_punch":
                chunk_size = 1
                style_to_use = "Punch"
            elif sub_mode == "fluid_flow":
                chunk_size = 4 if width > height else 3
                style_to_use = "Default"
            else:  # "dynamic_pop"
                chunk_size = 2 if width < height else 3
                style_to_use = "Default"
            if max_per_line and sub_mode != "single_word_punch":
                chunk_size = max_per_line

            # Recolectar palabras que caen en esta escena
            scene_words = []
            while word_idx < total_words:
                w = words[word_idx]
                if w.start <= s_end or s_idx == len(scenes) - 1:
                    scene_words.append(w)
                    word_idx += 1
                else:
                    break

            if not scene_words:
                continue

            chunks = self._create_chunks(scene_words, chunk_size)

            for chunk in chunks:
                if not chunk:
                    continue

                for active_idx, target_word in enumerate(chunk):
                    start_t = target_word.start
                    end_t = target_word.end

                    if active_idx == len(chunk) - 1:
                        end_t += 0.12

                    formatted_words = []
                    for i, w in enumerate(chunk):
                        word_text = escape_ass_text(w.word.upper() if use_upper else w.word)
                        if i == active_idx:
                            # 🚀 Efecto Bouncy Pop: escala elástica del 122% al 100% en los primeros 140ms
                            pop_tag = r"{\t(0,70,\fscx122\fscy122)\t(70,140,\fscx100\fscy100)}"
                            formatted_words.append(f"{pop_tag}{{\\c{accent_col}\\b1}}{word_text}{{\\c{self.inactive_color}\\b1}}")
                        else:
                            formatted_words.append(f"{{\\c{self.inactive_color}}}{word_text}")

                    events.append((start_t, end_t, style_to_use, " ".join(formatted_words)))

        dialogue = [
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},{style},,0,0,0,,{text}"
            for start, end, style, text in self._resolve_overlaps(events)
        ]
        # Capa superior: título-gancho en los primeros segundos y palabras clave que saltan.
        if title_card:
            dialogue.append(
                f"Dialogue: 2,{format_ass_time(0.0)},{format_ass_time(2.6)},Title,,0,0,0,,"
                + r"{\fad(0,250)\t(0,160,\fscx108\fscy108)\t(160,300,\fscx100\fscy100)}"
                + escape_ass_text(title_card.upper())
            )
        for start, end, text in pop_words or []:
            dialogue.append(
                f"Dialogue: 1,{format_ass_time(start)},{format_ass_time(end)},Pop,,0,0,0,,"
                + r"{\fad(60,150)\t(0,110,\fscx118\fscy118)\t(110,220,\fscx100\fscy100)}"
                + escape_ass_text(text.upper())
            )
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(dialogue) + "\n")

        return output_ass_path

    @staticmethod
    def _resolve_overlaps(events, max_gap_fill: float = 0.35, min_duration: float = 0.05):
        """Un solo evento visible a la vez: cada uno termina donde empieza el siguiente y
        los silencios cortos se rellenan para que el texto no parpadee entre palabras."""
        events = sorted(events, key=lambda e: e[0])
        resolved = []
        for i, (start, end, style, text) in enumerate(events):
            if i + 1 < len(events):
                next_start = events[i + 1][0]
                if end > next_start or next_start - end <= max_gap_fill:
                    end = next_start
            if end - start >= min_duration:
                resolved.append((start, end, style, text))
        return resolved

    def _create_chunks(self, words: List[WordTiming], chunk_size: int) -> List[List[WordTiming]]:
        """Divide la lista de palabras en grupos visuales balanceados cortando en pausas de puntuación."""
        if not words:
            return []

        chunks = []
        current_chunk = []

        for w in words:
            current_chunk.append(w)
            has_pause = any(w.word.endswith(p) for p in [".", "!", "?", ",", ";", ":"])
            if len(current_chunk) >= chunk_size or (len(current_chunk) >= 1 and has_pause and chunk_size > 1):
                chunks.append(current_chunk)
                current_chunk = []

        if current_chunk:
            chunks.append(current_chunk)

        return chunks


_POP_STOPWORDS = {"porque", "para", "como", "cuando", "donde", "este", "esta", "esto", "that", "this", "with",
                  "contexto", "explicacion", "inicio", "origen"}


def pick_pop_words(scenes: List[Any], words: List[WordTiming], min_gap: float = 2.5,
                   skip_until: float = 2.6, show_for: float = 1.0) -> List[tuple]:
    """Momentos en que una palabra clave o cifra salta en grande: se buscan las keywords de
    cada escena (o, si no hay, la primera cifra) en los tiempos reales de la voz."""
    import unicodedata

    def norm(text: str) -> str:
        plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")
        return "".join(ch for ch in plain if ch.isalnum() or ch == " ").strip()

    tokens = [norm(w.word) for w in words]
    pops, last = [], -min_gap
    for scene in scenes:
        # Solo palabras con peso visual: cifras o términos de 4+ letras que no sean palabras vacías.
        candidates = [k for k in (getattr(scene, "keywords", None) or []) if isinstance(k, str) and k.strip()
                      and (any(ch.isdigit() for ch in k) or (len(k.strip()) >= 4 and norm(k) not in _POP_STOPWORDS))]
        found = None
        for keyword in candidates:
            target = norm(keyword).split()
            if not target:
                continue
            for i in range(len(tokens) - len(target) + 1):
                if tokens[i:i + len(target)] == target and words[i].start >= skip_until and words[i].start - last >= min_gap:
                    found = (words[i].start, keyword)
                    break
            if found:
                break
        if found:
            pops.append((found[0], found[0] + show_for, found[1]))
            last = found[0]
    return pops
