"""Política de producción y planificación de planos visuales."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ProductionPolicy:
    profile: str = "balanced_fast"
    short_visual_seconds: float = 4.0
    long_visual_seconds: float = 8.0
    max_visual_beats: int = 60
    min_visual_seconds: float = 2.5
    provider_retries: int = 3
    network_concurrency: int = 2
    render_profile: str = "balanced"
    failure_policy: str = "resilient"

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None, profile: Optional[str] = None) -> "ProductionPolicy":
        values = dict((config or {}).get("production", {}))
        selected = profile or values.pop("profile", "balanced_fast")
        presets = {
            "very_fast": {"short_visual_seconds": 2.5, "long_visual_seconds": 6.0},
            "balanced_fast": {"short_visual_seconds": 2.8, "long_visual_seconds": 8.0},
            "cinematic": {"short_visual_seconds": 6.0, "long_visual_seconds": 12.0},
        }
        values.update(presets.get(selected, presets["balanced_fast"]))
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(profile=selected, **{key: value for key, value in values.items() if key in allowed})

    def target_visual_seconds(self, duration: float) -> float:
        """Duración de plano: ritmo corto en formatos de redes (hasta 3 min), más pausado en largos."""
        return self.short_visual_seconds if duration <= 180 else self.long_visual_seconds


SHOT_INTENTS = (
    "wide establishing composition",
    "intimate subject detail",
    "contextual environment",
    "concrete comparison or scale",
    "evidence and texture detail",
    "human consequence",
    "clean transition composition",
)


@dataclass(frozen=True)
class Shot:
    """Un plano del montaje. Una escena narrativa se divide en varios planos."""
    scene_index: int
    media_path: str
    duration: float
    camera_motion: str
    visual_vfx: str
    media_type: str
    transition_out: str


# Movimientos que se alternan al reutilizar la misma imagen en planos seguidos.
_SHOT_CAMERAS = ("slow_dolly_in", "pan_left", "fast_punch_zoom", "pan_right", "slow_pull_out")


def plan_shots(
    scenes: List[Any],
    scene_durations: List[float],
    scene_media: List[List[str]],
    shot_seconds: float,
    fps: int = 30,
    min_shot_seconds: float = 2.0,
    cut_points: Optional[List[float]] = None,
    sentence_cuts: Optional[List[List[Optional[float]]]] = None,
) -> List[Shot]:
    """Divide cada escena en planos de ~shot_seconds sin mover los cortes narrativos.

    Si la escena tiene varios medios (un clip de stock por frase, como los escribe el
    director) y se conocen los finales de frase, cada medio se muestra mientras se dice
    su frase. Si solo hay una imagen, los planos cambian de movimiento de cámara.
    El último plano de cada escena lleva la transición que eligió el director.
    """
    shots: List[Shot] = []
    scene_start = 0.0
    for i, (scene, duration) in enumerate(zip(scenes, scene_durations)):
        media = scene_media[i] if i < len(scene_media) and scene_media[i] else []
        if not media:
            continue
        frames = max(1, int(round(duration * fps)))
        sentences = sentence_cuts[i] if sentence_cuts and i < len(sentence_cuts) else None
        if len(media) > 1 and sentences:
            bounds, owners = _sentence_bounds(sentences, len(media), scene_start, duration, frames, fps, min_shot_seconds)
            scene_start += duration
            base_camera = getattr(scene, "camera_motion", "slow_dolly_in")
            for k, owner in enumerate(owners):
                shots.append(Shot(
                    scene_index=i,
                    media_path=media[owner],
                    duration=(bounds[k + 1] - bounds[k]) / fps,
                    camera_motion=base_camera if k == 0 else _SHOT_CAMERAS[(i + k) % len(_SHOT_CAMERAS)],
                    visual_vfx=getattr(scene, "visual_vfx", "cinematic_clean"),
                    media_type=getattr(scene, "media_type", "ai_image"),
                    transition_out=getattr(scene, "transition_out", "smash_cut") if k == len(owners) - 1 else "smash_cut",
                ))
            continue
        count = max(1, min(int(duration // max(min_shot_seconds, 0.5)), round(duration / max(shot_seconds, 0.5))))
        bounds = [round(frames * k / count) for k in range(count + 1)]
        if cut_points:
            # Cada corte interno se mueve a la pausa de la voz más cercana (±0.8 s): el cambio de
            # plano coincide con el final de una frase, como en una edición hecha a mano.
            for k in range(1, count):
                ideal = scene_start + bounds[k] / fps
                near = [c for c in cut_points if abs(c - ideal) <= 0.8 and scene_start + min_shot_seconds * 0.75 < c < scene_start + duration - min_shot_seconds * 0.75]
                if near:
                    snapped = int(round((min(near, key=lambda c: abs(c - ideal)) - scene_start) * fps))
                    if bounds[k - 1] < snapped < bounds[k + 1]:
                        bounds[k] = snapped
        scene_start += duration
        base_camera = getattr(scene, "camera_motion", "slow_dolly_in")
        for k in range(count):
            camera = base_camera if k == 0 else _SHOT_CAMERAS[(i + k) % len(_SHOT_CAMERAS)]
            if k > 0 and camera == shots[-1].camera_motion:
                camera = _SHOT_CAMERAS[(i + k + 1) % len(_SHOT_CAMERAS)]
            shots.append(Shot(
                scene_index=i,
                media_path=media[k % len(media)],
                duration=(bounds[k + 1] - bounds[k]) / fps,
                camera_motion=camera,
                visual_vfx=getattr(scene, "visual_vfx", "cinematic_clean"),
                media_type=getattr(scene, "media_type", "ai_image"),
                transition_out=getattr(scene, "transition_out", "smash_cut") if k == count - 1 else "smash_cut",
            ))
    return shots


def _sentence_bounds(cuts: List[Optional[float]], media_count: int, scene_start: float, duration: float,
                     frames: int, fps: int, min_shot_seconds: float):
    """Límites (en fotogramas) de un plano por frase y el medio que le toca a cada uno.

    cuts son los instantes absolutos entre frases (None si no se alinearon). Las frases
    demasiado cortas se unen a la anterior. Cuando hay más frases que medios, el medio j
    se reparte en proporción para que el orden de las búsquedas del director se respete.
    """
    sentence_count = len(cuts) + 1
    bounds, owners = [0], [0]
    for j, cut in enumerate(cuts, start=1):
        if cut is None:
            continue
        local = cut - scene_start
        if local - bounds[-1] / fps >= min_shot_seconds and duration - local >= min_shot_seconds:
            bounds.append(int(round(local * fps)))
            owners.append(min(media_count - 1, j * media_count // sentence_count))
    bounds.append(frames)
    return bounds, owners
