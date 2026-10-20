"""
Motion Engine Module
Genera clips por escena: movimientos de cámara sobre imágenes (Ken Burns / 2.5D),
color grading (VFX) y normalización de clips de stock.

Todos los clips se codifican con parámetros idénticos y un número exacto de
fotogramas, para poder unirlos sin recodificar y sin deriva de sincronía.
"""

import os
import math
import subprocess
from typing import List, Optional

# Parámetros de codificación compartidos por todos los clips intermedios.
CLIP_TIMESCALE = "90000"
# Nitidez leve: compensa el reescalado y la compresión sin crear halos.
SHARPEN = ",unsharp=5:5:0.6:5:5:0.0"


def frames_for(duration: float, fps: int) -> int:
    return max(1, int(round(duration * fps)))


class MotionEngine:
    def __init__(self, fps: int = 30):
        self.fps = fps

    # ------------------------------------------------------------------ util
    def _encode_args(self, frames: int, output_path: str) -> List[str]:
        return [
            "-frames:v", str(frames),
            "-r", str(self.fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-video_track_timescale", CLIP_TIMESCALE,
            "-an",
            output_path,
        ]

    def _run(self, cmd: List[str], fallback: List[str], label: str) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return
        print(f"[MotionEngine] {label} falló, usando versión simple: {result.stderr.strip()[-300:]}")
        fb = subprocess.run(fallback, capture_output=True, text=True)
        if fb.returncode != 0:
            raise RuntimeError(f"FFmpeg no pudo crear el clip ({label}): {fb.stderr.strip()[-400:]}")

    def _static_cmd(self, image_path: str, frames: int, output_clip_path: str, width: int, height: int, vfx_chain: str = "") -> List[str]:
        vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1{vfx_chain}"
        return ["ffmpeg", "-y", "-loop", "1", "-framerate", str(self.fps), "-i", image_path, "-vf", vf] + self._encode_args(frames, output_clip_path)

    def _camera_cmd(self, image_path: str, frames: int, output_clip_path: str, width: int, height: int,
                    zoom_expr: str, dx_expr: str, dy_expr: str, vfx_chain: str) -> List[str]:
        """Cámara virtual con precisión de subpíxel (filtro perspective, interpolación cúbica).

        zoompan redondea la ventana a píxeles enteros y el movimiento avanza "a saltos"
        (medido: irregularidad 0.18 entre fotogramas). perspective interpola por debajo del
        píxel: 0.08, más nítido y casi igual de rápido.
        zoom_expr: factor de zoom (>=1). dx/dy: desplazamiento del centro en fracción del ancho/alto;
        se limita para que la ventana nunca salga de la imagen.
        """
        mx = f"((1-1/({zoom_expr}))/2)"
        dx = f"max(-{mx},min({mx},{dx_expr}))"
        dy = f"max(-{mx},min({mx},{dy_expr}))"
        left, right = f"W*({mx}+{dx})", f"W*(1-{mx}+{dx})"
        top, bottom = f"H*({mx}+{dy})", f"H*(1-{mx}+{dy})"
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,"
            f"perspective=x0='{left}':y0='{top}':x1='{right}':y1='{top}':x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}'"
            f":interpolation=cubic:eval=frame{vfx_chain}"
        )
        return ["ffmpeg", "-y", "-loop", "1", "-framerate", str(self.fps), "-i", image_path, "-vf", vf] + self._encode_args(frames, output_clip_path)

    @staticmethod
    def _progress(frames: int, span: float = 1.0) -> str:
        """Progreso 0->1 con aceleración y frenado suaves (smoothstep) a lo largo de `span` del plano."""
        u = f"min(1,on/{max(1.0, (frames - 1) * span):.2f})"
        return f"({u}*{u}*(3-2*{u}))"

    # ------------------------------------------------------------------ cámaras
    def create_directed_scene_clip(
        self,
        image_path: str,
        duration: float,
        output_clip_path: str,
        width: int = 1080,
        height: int = 1920,
        camera_motion: str = "fast_punch_zoom",
        visual_vfx: str = "cinematic_clean",
        zoom_intensity: float = 1.0,
    ) -> str:
        """Ejecuta el movimiento de cámara y el color grading pedidos para esta escena."""
        os.makedirs(os.path.dirname(os.path.abspath(output_clip_path)), exist_ok=True)
        frames = frames_for(duration, self.fps)
        vfx_chain = self._resolve_vfx_filter(visual_vfx)
        static = self._static_cmd(image_path, frames, output_clip_path, width, height, vfx_chain)

        k = max(0.0, float(zoom_intensity))
        if camera_motion == "static_cinematic" or k <= 0.05:
            self._run(static, static, "plano estático")
            return output_clip_path

        p = self._progress(frames)
        # El recorrido total es el mismo en un plano de 2 s que en uno de 10 s.
        if camera_motion == "fast_punch_zoom":
            punch = f"(1-pow(1-min(1,on/{max(1.0, frames * 0.45):.2f}),3))"   # entra rápido y frena (ease-out)
            zoom, dx, dy = f"1+{0.22 * k:.4f}*{punch}", "0", "0"
        elif camera_motion == "slow_dolly_in":
            zoom, dx, dy = f"1+{0.12 * k:.4f}*{p}", "0", "0"
        elif camera_motion == "slow_pull_out":
            zoom, dx, dy = f"1+{0.15 * k:.4f}*(1-{p})", "0", "0"
        elif camera_motion in ("pan_left", "pan_right"):
            direction = -1 if camera_motion == "pan_left" else 1
            zoom = f"{1 + 0.14 * max(k, 0.5):.4f}"
            dx, dy = f"{direction}*({p}-0.5)*{0.10 * max(k, 0.5):.4f}", "0"
        elif camera_motion == "camera_shake":
            # Cámara en mano: deriva lenta (<1 Hz) con dos frecuencias; una oscilación rápida se ve como vibración.
            zoom = "1.10"
            dx = f"{0.008 * k:.4f}*(sin(on*0.13)+0.4*sin(on*0.29))"
            dy = f"{0.006 * k:.4f}*(cos(on*0.11)+0.4*sin(on*0.23))"
        else:
            zoom, dx, dy = f"1+{0.14 * k:.4f}*{p}", "0", "0"

        cmd = self._camera_cmd(image_path, frames, output_clip_path, width, height, zoom, dx, dy, vfx_chain)
        self._run(cmd, static, f"cámara {camera_motion}")
        return output_clip_path

    def create_3d_parallax_clip(
        self,
        image_path: str,
        duration: float,
        output_clip_path: str,
        width: int = 1080,
        height: int = 1920,
        motion_type: str = "dolly_in_depth",
        visual_vfx: str = "cinematic_clean",
        zoom_intensity: float = 1.0,
    ) -> str:
        """Movimiento 2.5D: avance con una deriva lenta que simula una cámara en profundidad."""
        os.makedirs(os.path.dirname(os.path.abspath(output_clip_path)), exist_ok=True)
        frames = frames_for(duration, self.fps)
        k = max(0.0, float(zoom_intensity))
        vfx_chain = self._resolve_vfx_filter(visual_vfx)
        p = self._progress(frames)

        if motion_type == "orbit_3d":
            zoom = f"1.10+{0.04 * k:.3f}*{p}"
            dx, dy = f"{0.03 * k:.4f}*({p}-0.5)", f"{0.01 * k:.4f}*sin(PI*{p})"
        elif motion_type == "pull_out_reveal":
            zoom, dx, dy = f"1+{0.18 * k:.4f}*(1-{p})", "0", "0"
        else:  # dolly_in_depth
            zoom = f"1+{0.20 * k:.4f}*{p}"
            dx, dy = f"{0.012 * k:.4f}*({p}-0.5)", f"{-0.008 * k:.4f}*({p}-0.5)"

        cmd = self._camera_cmd(image_path, frames, output_clip_path, width, height, zoom, dx, dy, vfx_chain)
        static = self._static_cmd(image_path, frames, output_clip_path, width, height, vfx_chain)
        self._run(cmd, static, "3D parallax")
        return output_clip_path

    def prepare_video_clip(
        self,
        input_video_path: str,
        duration: float,
        output_clip_path: str,
        width: int = 1080,
        height: int = 1920,
        visual_vfx: str = "cinematic_clean",
        exposure_target: Optional[int] = None,
    ) -> str:
        """Adapta un clip de stock a la duración, resolución, fps, exposición y grading del video."""
        os.makedirs(os.path.dirname(os.path.abspath(output_clip_path)), exist_ok=True)
        frames = frames_for(duration, self.fps)
        exposure = self._stock_exposure_filter(input_video_path, exposure_target) if exposure_target else ""
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={self.fps}"
            f"{exposure}{self._resolve_vfx_filter(visual_vfx)}"
        )
        # -stream_loop repite el clip si es más corto que la escena.
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", input_video_path, "-vf", vf] + self._encode_args(frames, output_clip_path)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"No se pudo adaptar el clip de stock: {result.stderr.strip()[-400:]}")
        return output_clip_path

    @staticmethod
    def _stock_exposure_filter(video_path: str, target: int) -> str:
        """Mide un fotograma del clip y, si está oscuro, devuelve un filtro eq de gamma."""
        from PIL import Image
        from src.core.media_engine import exposure_gamma
        probe = video_path + ".probe.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", video_path, "-frames:v", "1",
                            "-vf", "scale=240:-2", probe], capture_output=True, timeout=30)
            with Image.open(probe) as frame:
                gamma = exposure_gamma(frame.convert("RGB"), target)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        finally:
            if os.path.exists(probe):
                os.remove(probe)
        if gamma >= 0.99:
            return ""
        return f",eq=gamma={1 / gamma:.3f}:saturation={1.0 + (1.0 - gamma) * 0.5:.3f}"

    def _resolve_vfx_filter(self, visual_vfx: str) -> str:
        """Filtros de colorimetría FFmpeg según la directiva de la IA, más nitidez leve.

        Las viñetas y el blanco y negro se suavizaron: antes oscurecían tanto los bordes
        que en el móvil el video se veía apagado."""
        grades = {
            "deep_vignette": ",eq=contrast=1.06:saturation=1.1,vignette=PI/6",
            "teal_orange": ",eq=contrast=1.08:saturation=1.18,colorbalance=rs=0.06:gs=0.02:bs=-0.06:rm=-0.03:gm=0.01:bm=0.06",
            "film_grain": ",eq=contrast=1.04:saturation=1.08,noise=alls=6:allf=t+u",
            "warm_vintage": ",eq=contrast=1.04:saturation=1.0,colorbalance=rs=0.08:gs=0.03:bs=-0.06",
            "noir_bw": ",hue=s=0,eq=contrast=1.15:brightness=0.02",
        }
        grade = grades.get(visual_vfx, ",eq=contrast=1.05:saturation=1.1:brightness=0.01")
        return grade + SHARPEN
