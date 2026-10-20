"""
Audio Engine Module
Mezcla profesional de audio con diseño sonoro (SFX) sincronizado según las órdenes del Director de IA,
masterización vocal de alta presencia y auto-ducking con compresión sidechain reactiva.
"""

import os
import subprocess
import random
from typing import Optional, List, Dict, Any

# Volumen integrado estándar de TikTok/Reels/Shorts, con techo de picos seguro.
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"


def _codec_args(path: str) -> List[str]:
    """WAV intermedio sin pérdidas; MP3 si el llamador lo pide explícitamente."""
    if path.lower().endswith(".wav"):
        return ["-c:a", "pcm_s16le", "-ar", "48000"]
    return ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "48000"]

class AudioEngine:
    def __init__(self, music_dir: str = "assets/music", sfx_dir: str = "assets/sfx"):
        self.music_dir = music_dir
        self.sfx_dir = sfx_dir
        os.makedirs(self.music_dir, exist_ok=True)
        os.makedirs(self.sfx_dir, exist_ok=True)
        self._ensure_default_tracks()
        self._ensure_default_sfx()

    def get_music_track(self, category: Optional[str] = None) -> Optional[str]:
        """Obtiene una pista de música aleatoria o por categoría si existe."""
        if not os.path.exists(self.music_dir):
            return None

        valid_exts = (".mp3", ".wav", ".aac", ".m4a")
        tracks = [
            os.path.join(self.music_dir, f)
            for f in os.listdir(self.music_dir)
            if f.lower().endswith(valid_exts)
        ]

        if not tracks:
            return None

        return random.choice(tracks)

    def mix_directed_audio(
        self,
        voice_path: str,
        output_mixed_path: str,
        total_duration: float,
        scene_durations: List[float],
        sfx_cues: List[str],
        music_path: Optional[str] = None,
        music_volume: float = 0.12,
        fade_out: float = 1.5,
        use_music: bool = True,
        sfx_mode: str = "full",
    ) -> str:
        """
        Ensambla el diseño sonoro completo:
        1. Masterización de la voz (ecualización de presencia y control dinámico).
        2. Inserción de SFX en los segundos exactos dictados por el Director de IA.
        3. Compresión sidechain reactiva (la música baja al hablar y sube en las pausas).
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_mixed_path)), exist_ok=True)
        bg_music = (music_path or self.get_music_track()) if use_music else None
        if sfx_mode == "off":
            sfx_cues = []
        elif sfx_mode == "minimal":
            # Solo un impacto en el gancho y whooshes suaves en los cambios de escena.
            sfx_cues = ["impact_boom" if i == 0 else ("whoosh_fast" if cue not in ("none", "", None) else "none")
                        for i, cue in enumerate(sfx_cues)]

        # Calcular marcas de tiempo (en milisegundos) para cada escena
        scene_timestamps_ms = []
        accum = 0.0
        for d in scene_durations:
            scene_timestamps_ms.append(int(accum * 1000))
            accum += d

        # Recopilar eventos SFX activos
        active_sfx = []
        for i, cue in enumerate(sfx_cues):
            if cue and cue not in ("none", "silence", "", None):
                sfx_file = os.path.join(self.sfx_dir, f"{cue}.wav")
                if os.path.exists(sfx_file):
                    delay_ms = scene_timestamps_ms[i] if i < len(scene_timestamps_ms) else 0
                    active_sfx.append({
                        "file": sfx_file,
                        "delay_ms": delay_ms,
                        "cue": cue
                    })

        # Limitar número de SFX simultáneos a un máximo de 8 para evitar sobrecargar filtros de audio
        active_sfx = active_sfx[:8]

        # Si no hay música de fondo ni SFX, solo masterizar la voz
        if not bg_music and not active_sfx:
            return self._master_voice_standalone(voice_path, output_mixed_path, total_duration)

        # Construir comando FFmpeg con filtro complejo
        cmd = ["ffmpeg", "-y", "-i", voice_path]
        input_idx = 1

        if bg_music and os.path.exists(bg_music):
            cmd.extend(["-stream_loop", "-1", "-i", bg_music])
            music_input_idx = input_idx
            input_idx += 1
        else:
            music_input_idx = None

        sfx_indices = []
        for sfx in active_sfx:
            cmd.extend(["-i", sfx["file"]])
            sfx_indices.append((input_idx, sfx["delay_ms"]))
            input_idx += 1

        # Construcción de la cadena de filtros
        fade_out_start = max(0.1, total_duration - fade_out)
        filter_parts = []

        # 1. Masterizar locución: eliminar ruidos subgraves y realzar claridad vocal (3.5 kHz)
        filter_parts.append(
            "[0:a]highpass=f=75,equalizer=f=3500:t=q:w=1.5:g=2.2,acompressor=threshold=0.15:ratio=3:attack=15:release=180[voice_proc]"
        )

        mix_inputs = ["[voice_proc]"]

        # 2. Procesar música con compresión Sidechain real o auto-ducking
        if music_input_idx is not None:
            filter_parts.append(
                f"[{music_input_idx}:a]volume={music_volume},afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.2f}:d={fade_out}[bgm_raw]"
            )
            # Aplicar sidechain si la voz está activa
            filter_parts.append(
                "[voice_proc]asplit=2[v_main][v_sc];"
                "[bgm_raw][v_sc]sidechaincompress=threshold=0.07:ratio=4.5:attack=80:release=350[bgm_ducked]"
            )
            # Reemplazar [voice_proc] con [v_main]
            mix_inputs = ["[v_main]", "[bgm_ducked]"]

        # 3. Procesar y retrasar cada SFX a su milisegundo exacto
        for idx, (sfx_in, delay_ms) in enumerate(sfx_indices):
            sfx_out = f"sfx_{idx}"
            filter_parts.append(
                f"[{sfx_in}:a]adelay={delay_ms}|{delay_ms},volume=0.40[{sfx_out}]"
            )
            mix_inputs.append(f"[{sfx_out}]")

        # 4. Mezclar todos los canales
        num_inputs = len(mix_inputs)
        mix_str = "".join(mix_inputs)
        filter_parts.append(
            f"{mix_str}amix=inputs={num_inputs}:duration=first:dropout_transition=2:normalize=0,{LOUDNORM}[aout]"
        )

        filter_complex = ";".join(filter_parts)

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-t", f"{total_duration:.3f}",
            *_codec_args(output_mixed_path),
            output_mixed_path
        ])

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return output_mixed_path
        except subprocess.CalledProcessError as e:
            print(f"[AudioEngine] Advertencia en mezcla avanzada: {e.stderr[-400:]}. Usando fallback limpio.")
            # Fallback seguro
            return self.mix_voice_and_music(voice_path, output_mixed_path, total_duration, bg_music, music_volume, fade_out)

    def mix_voice_and_music(
        self,
        voice_path: str,
        output_mixed_path: str,
        total_duration: float,
        music_path: Optional[str] = None,
        music_volume: float = 0.12,
        fade_out: float = 1.5
    ) -> str:
        """Mezcla estándar de respaldo con auto-ducking básico."""
        os.makedirs(os.path.dirname(os.path.abspath(output_mixed_path)), exist_ok=True)
        bg_music = music_path or self.get_music_track()

        if not bg_music or not os.path.exists(bg_music):
            return self._master_voice_standalone(voice_path, output_mixed_path, total_duration)

        fade_out_start = max(0.1, total_duration - fade_out)
        cmd = [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-stream_loop", "-1", "-i", bg_music,
            "-filter_complex",
            f"[1:a]volume={music_volume},afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start:.2f}:d={fade_out}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,{LOUDNORM}[aout]",
            "-map", "[aout]",
            "-t", f"{total_duration:.3f}",
            *_codec_args(output_mixed_path),
            output_mixed_path
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return output_mixed_path
        except Exception:
            return self._master_voice_standalone(voice_path, output_mixed_path, total_duration)

    def _master_voice_standalone(self, voice_path: str, output_path: str, duration: float) -> str:
        """Aplica masterización a la pista de voz sin acompañamiento musical."""
        cmd = [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-af", f"highpass=f=75,equalizer=f=3500:t=q:w=1.5:g=2.2,acompressor=threshold=0.15:ratio=3:attack=15:release=180,{LOUDNORM}",
            "-t", f"{duration:.3f}",
            *_codec_args(output_path),
            output_path
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            return output_path
        except Exception:
            return voice_path

    def _ensure_default_tracks(self):
        """Crea un pad ambiental armónico si no hay música en assets/music."""
        tracks = [f for f in os.listdir(self.music_dir) if f.endswith((".mp3", ".wav"))]
        if not tracks:
            sample_track = os.path.join(self.music_dir, "ambient_pulse.wav")
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "sine=frequency=110:duration=60,volume=0.08,chorus=0.5:0.9:50|60:0.4|0.32:0.25|0.4:2|1.2",
                sample_track
            ]
            try:
                subprocess.run(cmd, capture_output=True, check=True)
            except Exception:
                pass

    def _ensure_default_sfx(self):
        """Sintetiza de forma autónoma la librería esencial de SFX cinematográficos en assets/sfx."""
        sfx_defs = {
            "impact_boom": "sine=frequency=65:duration=1.8,afade=t=out:st=0.2:d=1.6,volume=0.7",
            "whoosh_fast": "anoisesrc=d=0.7:c=white:a=0.3,bandpass=f=1200:w=800,afade=t=in:st=0:d=0.2,afade=t=out:st=0.25:d=0.45,volume=0.6",
            "cinematic_riser": "sine=frequency=160:duration=2.5,asetrate=44100*1.8,aresample=44100,afade=t=in:st=0:d=1.8,afade=t=out:st=2.2:d=0.3,volume=0.45",
            "sub_bass_drop": "sine=frequency=48:duration=2.0,afade=t=out:st=0.3:d=1.7,volume=0.75",
            "glitch_sfx": "anoisesrc=d=0.5:c=pink:a=0.25,tremolo=f=28:d=0.8,afade=t=out:st=0.1:d=0.4,volume=0.5"
        }

        for sfx_name, lavfi_expr in sfx_defs.items():
            target_path = os.path.join(self.sfx_dir, f"{sfx_name}.wav")
            if not os.path.exists(target_path):
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", lavfi_expr, target_path]
                try:
                    subprocess.run(cmd, capture_output=True, check=True)
                except Exception:
                    pass

