"""Mejora de imágenes con Real-ESRGAN (ncnn-vulkan, funciona en GPU AMD/NVIDIA/Intel).

Se usa el modelo ligero realesr-animevideov3 x2: medido en la GPU integrada de este equipo
tarda ~14 s por imagen de 720x1280 y limpia los bloques de compresión JPEG de Pollinations.
El modelo fotográfico realesrgan-x4plus tardó 193 s: inviable, por eso no se usa.
Si el binario no está o falla, se devuelve None y el flujo sigue con la imagen original.
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Optional

DEFAULT_BINARY = os.path.join("tools", "realesrgan", "realesrgan-ncnn-vulkan.exe")
# Una sola GPU: una mejora a la vez por proceso.
_gpu_lock = threading.Lock()


class Upscaler:
    def __init__(self, binary: str = DEFAULT_BINARY, model: str = "realesr-animevideov3",
                 scale: int = 2, timeout: int = 90, enabled: bool = True):
        self.binary = binary
        self.model = model
        self.scale = scale
        self.timeout = timeout
        self.enabled = enabled

    @property
    def available(self) -> bool:
        return self.enabled and os.path.exists(self.binary)

    def upscale(self, input_path: str, output_path: str) -> Optional[str]:
        if not self.available:
            return None
        models_dir = os.path.join(os.path.dirname(os.path.abspath(self.binary)), "models")
        cmd = [os.path.abspath(self.binary), "-i", os.path.abspath(input_path), "-o", os.path.abspath(output_path),
               "-n", self.model, "-s", str(self.scale), "-m", models_dir, "-f", "png"]
        try:
            with _gpu_lock:
                result = subprocess.run(cmd, capture_output=True, timeout=self.timeout)
        except (subprocess.TimeoutExpired, OSError) as error:
            print(f"[Upscaler] Omitido ({type(error).__name__}); se usa la imagen original.")
            return None
        if result.returncode != 0 or not os.path.exists(output_path):
            print("[Upscaler] Real-ESRGAN falló; se usa la imagen original.")
            return None
        return output_path
