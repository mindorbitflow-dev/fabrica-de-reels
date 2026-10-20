"""
Video Renderer Module
Ensambla el video final: un clip por escena (cámara + VFX), transiciones xfade,
subtítulos ASS quemados y audio masterizado, en una única codificación final.

Sincronía: cada clip se alarga lo que dura su transición de salida, de modo que
la transición empieza exactamente en el corte narrativo y la duración total del
video coincide con la del audio.
"""

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import List, Any, Optional

from src.core.motion_engine import frames_for

# transición del director -> (efecto xfade de FFmpeg, duración en segundos)
XFADE_MAP = {
    "crossfade": ("fade", 0.35),
    "whip_pan_right": ("slideright", 0.25),
    "whip_pan_left": ("slideleft", 0.25),
    "flash_white": ("fadewhite", 0.30),
    "zoom_in": ("zoomin", 0.30),
    "glitch": ("pixelize", 0.22),
}

# (preset, CRF) de libx264. Las plataformas recodifican al subir, así que "balanced"
# prioriza velocidad: veryfast/20 tardó 31s frente a 41s de fast/20 en un reel de 28s.
RENDER_PROFILES = {
    "draft": ("ultrafast", "23"),
    "balanced": ("veryfast", "18"),
    "quality": ("medium", "18"),
}

# Codificadores por hardware, en orden de preferencia. Medido con AMD AMF: 12s frente
# a 31s de libx264 en el mismo reel, con SSIM 0.98.
HW_ENCODERS = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
    "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", "19"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20"],
}
_encoder_cache = {}


def detect_encoder(preferred: str = "auto") -> str:
    """Devuelve el primer codificador H.264 que realmente funciona en esta máquina."""
    if preferred in ("libx264", "cpu"):
        return "libx264"
    if preferred in _encoder_cache:
        return _encoder_cache[preferred]
    candidates = [preferred] if preferred in HW_ENCODERS else list(HW_ENCODERS)
    chosen = "libx264"
    for name in candidates:
        probe = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=0.2",
                 *HW_ENCODERS[name], "-pix_fmt", "yuv420p", "-f", "null", "-"]
        try:
            if subprocess.run(probe, capture_output=True, timeout=30).returncode == 0:
                chosen = name
                break
        except (subprocess.TimeoutExpired, OSError):
            continue
    _encoder_cache[preferred] = chosen
    return chosen

VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv")


def escape_ffmpeg_path(path: str) -> str:
    """Escapa una ruta para usarla dentro de un filtro FFmpeg en Windows."""
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p.replace("'", "'\\''").replace("[", "\\[").replace("]", "\\]")


class VideoRenderer:
    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30,
                 render_profile: str = "balanced", workers: int = 2, max_graph_clips: int = 40,
                 fonts_dir: str = "assets/fonts", encoder: str = "auto", progress_bar: bool = True):
        self.width = width
        self.height = height
        self.fps = fps
        self.render_profile = render_profile
        self.workers = max(1, workers)
        self.max_graph_clips = max_graph_clips
        self.fonts_dir = fonts_dir
        self.encoder_preference = encoder
        self.progress_bar = progress_bar

    # ------------------------------------------------------------------ planificación
    def transition_pads(self, transitions: List[str], durations: List[float]) -> List[float]:
        """Segundos extra que necesita cada clip para su transición de salida (0 en cortes secos)."""
        n = len(durations)
        if n <= 1 or n > self.max_graph_clips:
            return [0.0] * n
        pads = []
        for i in range(n):
            if i == n - 1:
                pads.append(0.0)
                continue
            _, t = XFADE_MAP.get(transitions[i] if i < len(transitions) else "smash_cut", (None, 0.0))
            # Nunca más del 40% de la escena más corta de las dos que se funden.
            t = min(t, 0.4 * durations[i], 0.4 * durations[i + 1])
            pads.append(round(t * self.fps) / self.fps)
        return pads

    # ------------------------------------------------------------------ render
    def render_directed_reel(
        self,
        scene_images: List[str],
        scene_durations: List[float],
        scenes: List[Any],
        audio_path: str,
        subtitles_path: Optional[str],
        output_video_path: str,
        total_duration: float,
        motion_engine: Any,
        width: Optional[int] = None,
        height: Optional[int] = None,
        work_dir: Optional[str] = None,
        zoom_intensity: float = 1.0,
        warnings: Optional[List[str]] = None,
        exposure_target: Optional[int] = None,
    ) -> str:
        """
        scene_images: por escena, la ruta de una imagen o de un video de stock sin procesar.
        """
        w = width or self.width
        h = height or self.height
        warnings = warnings if warnings is not None else []
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        temp_dir = os.path.join(work_dir or os.path.splitext(output_video_path)[0] + "_tmp", "render_tmp")
        os.makedirs(temp_dir, exist_ok=True)

        transitions = [getattr(s, "transition_out", "smash_cut") for s in scenes]
        pads = self.transition_pads(transitions, scene_durations)

        # 1. Un clip por escena, en paralelo (cada FFmpeg ya usa varios hilos).
        def build_clip(i: int) -> str:
            media_path = scene_images[i]
            scene = scenes[i] if i < len(scenes) else None
            clip_path = os.path.join(temp_dir, f"clip_{i:03d}.mp4")
            clip_dur = scene_durations[i] + pads[i]
            vfx = getattr(scene, "visual_vfx", "cinematic_clean")
            if media_path.lower().endswith(VIDEO_EXTENSIONS):
                return motion_engine.prepare_video_clip(media_path, clip_dur, clip_path, w, h, visual_vfx=vfx,
                                                        exposure_target=exposure_target)
            if getattr(scene, "media_type", "ai_image") == "3d_parallax":
                return motion_engine.create_3d_parallax_clip(
                    media_path, clip_dur, clip_path, w, h,
                    motion_type=("dolly_in_depth", "orbit_3d", "pull_out_reveal")[i % 3],
                    visual_vfx=vfx, zoom_intensity=zoom_intensity,
                )
            return motion_engine.create_directed_scene_clip(
                media_path, clip_dur, clip_path, w, h,
                camera_motion=getattr(scene, "camera_motion", "slow_dolly_in"),
                visual_vfx=vfx, zoom_intensity=zoom_intensity,
            )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            clips = list(pool.map(build_clip, range(len(scene_images))))

        # 2. Ensamble + subtítulos + audio en una sola codificación.
        subs = subtitles_path if subtitles_path and os.path.exists(subtitles_path) else None
        try:
            self._final_encode(clips, scene_durations, pads, transitions, audio_path, subs, output_video_path, total_duration, temp_dir)
        except RuntimeError as error:
            if not subs:
                raise
            warnings.append(f"Los subtítulos no se pudieron quemar ({str(error)[-160:]}); se exportó sin subtítulos.")
            print(f"[VideoRenderer] {warnings[-1]}")
            self._final_encode(clips, scene_durations, pads, transitions, audio_path, None, output_video_path, total_duration, temp_dir)

        shutil.rmtree(temp_dir, ignore_errors=True)
        return output_video_path

    def _video_args(self, encoder: str) -> List[str]:
        if encoder in HW_ENCODERS:
            return list(HW_ENCODERS[encoder])
        preset, crf = RENDER_PROFILES.get(self.render_profile, RENDER_PROFILES["balanced"])
        return ["-c:v", "libx264", "-preset", preset, "-crf", crf]

    def _final_encode(self, clips, durations, pads, transitions, audio_path, subtitles_path,
                      output_path, total_duration, temp_dir) -> None:
        encoder = detect_encoder(self.encoder_preference)
        try:
            self._final_encode_with(encoder, clips, durations, pads, transitions, audio_path, subtitles_path, output_path, total_duration, temp_dir)
        except RuntimeError:
            if encoder == "libx264":
                raise
            print(f"[VideoRenderer] {encoder} falló; reintentando con libx264.")
            _encoder_cache[self.encoder_preference] = "libx264"
            self._final_encode_with("libx264", clips, durations, pads, transitions, audio_path, subtitles_path, output_path, total_duration, temp_dir)

    def _final_encode_with(self, encoder, clips, durations, pads, transitions, audio_path, subtitles_path,
                           output_path, total_duration, temp_dir) -> None:
        sub_filter = self._subtitles_filter(subtitles_path) if subtitles_path else ""
        # Barra de progreso: overlay recalculado por fotograma (drawbox evalúa el ancho una sola vez).
        bar = (f"[bar_in][barsrc]overlay=x='-w+w*t/{total_duration:.3f}':y=H-h:eval=frame:shortest=1"
               if self.progress_bar else "")
        tail = [
            *self._video_args(encoder), "-pix_fmt", "yuv420p", "-r", str(self.fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            "-t", f"{total_duration:.3f}",
        ]
        output_path = os.path.abspath(output_path)
        tmp_out = output_path + ".part.mp4"

        use_graph = any(p > 0 for p in pads)
        if use_graph:
            cmd = ["ffmpeg", "-y"]
            for clip in clips:
                cmd += ["-i", os.path.abspath(clip)]
            cmd += ["-i", os.path.abspath(audio_path)]
            graph = self._transition_graph(len(clips), durations, pads, transitions)
            graph, last = self._finish_graph(graph, "[vx]", sub_filter, bar)
            cmd += ["-filter_complex", graph, "-map", last, "-map", f"{len(clips)}:a"] + tail + [tmp_out]
            result = self._run(cmd, temp_dir)
            if result.returncode == 0:
                os.replace(tmp_out, output_path)
                return
            print(f"[VideoRenderer] Grafo de transiciones falló, usando cortes secos: {result.stderr.strip()[-300:]}")

        # Cortes secos: concat demuxer con outpoint exacto (descarta el relleno de transición).
        concat_txt = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_txt, "w", encoding="utf-8") as file:
            for clip, duration in zip(clips, durations):
                file.write(f"file '{os.path.abspath(clip).replace(chr(92), '/')}'\n")
                file.write(f"outpoint {duration:.4f}\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", os.path.abspath(concat_txt), "-i", os.path.abspath(audio_path)]
        graph, last = self._finish_graph("[0:v]null[vx]", "[vx]", sub_filter, bar)
        cmd += ["-filter_complex", graph, "-map", last, "-map", "1:a"] + tail + [tmp_out]
        result = self._run(cmd, temp_dir)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg no pudo exportar el video: {result.stderr.strip()[-500:]}")
        os.replace(tmp_out, output_path)

    def _finish_graph(self, graph: str, last: str, sub_filter: str, bar: str):
        if sub_filter:
            graph += f";{last}{sub_filter}[vsub]"
            last = "[vsub]"
        if bar:
            graph += f";color=c=0xFFD400:s={self.width}x12:r={self.fps}[barsrc];{last}null[bar_in];{bar}[vbar]"
            last = "[vbar]"
        return graph, last

    def _transition_graph(self, n: int, durations: List[float], pads: List[float], transitions: List[str]) -> str:
        parts = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[s{i}]" for i in range(n)]
        acc = "[s0]"
        cut_time = 0.0
        for k in range(1, n):
            cut_time += durations[k - 1]
            out = "[vx]" if k == n - 1 else f"[a{k}]"
            if pads[k - 1] > 0:
                effect, _ = XFADE_MAP[transitions[k - 1]]
                parts.append(f"{acc}[s{k}]xfade=transition={effect}:duration={pads[k - 1]:.4f}:offset={cut_time:.4f}{out}")
            else:
                parts.append(f"{acc}[s{k}]concat=n=2:v=1:a=0{out}")
            acc = out
        if n == 1:
            parts.append("[s0]null[vx]")
        return ";".join(parts)

    def _subtitles_filter(self, subtitles_path: str) -> str:
        sub = f"subtitles='{escape_ffmpeg_path(subtitles_path)}'"
        if os.path.isdir(self.fonts_dir) and os.listdir(self.fonts_dir):
            sub += f":fontsdir='{escape_ffmpeg_path(self.fonts_dir)}'"
        return sub

    @staticmethod
    def _run(cmd: List[str], cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)


def quantize_durations(durations: List[float], fps: int) -> List[float]:
    """Ajusta cada duración a fotogramas enteros sin acumular error: los cortes
    caen en el fotograma más cercano a su tiempo absoluto."""
    result, cursor, prev_frame = [], 0.0, 0
    for d in durations:
        cursor += d
        frame = max(prev_frame + 1, int(round(cursor * fps)))
        result.append((frame - prev_frame) / fps)
        prev_frame = frame
    return result


__all__ = ["VideoRenderer", "XFADE_MAP", "quantize_durations", "escape_ffmpeg_path", "frames_for"]
