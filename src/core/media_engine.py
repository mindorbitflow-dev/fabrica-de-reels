"""
Media Engine Module
Genera imágenes por IA (Pollinations Flux), descarga videos de stock (Pexels)
o prepara fondos de respaldo en la resolución del video.
"""

import os
import re
import math
import time
import random
import hashlib
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Set

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageStat

from src.core.env import load_env
from src.core.upscaler import Upscaler

WATERMARK_BAND = 0.08
USER_AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIReelFactory/2.0"}
PEXELS_STOPWORDS = {
    "4k", "8k", "hd", "cinematic", "photorealistic", "ultra", "masterpiece", "footage", "video",
    "render", "unreal", "octane", "lighting", "detailed", "style", "documentary", "vertical",
}


@dataclass
class MediaResult:
    path: str
    kind: str            # "image" | "video"
    is_fallback: bool = False
    note: str = ""


class MediaEngine:
    # Pollinations tarda ~45s por imagen 1080x1920 (medido); 35s cortaba casi siempre.
    IMAGE_TIMEOUT = 120
    PEXELS_TIMEOUT = 20
    # Luminancia media objetivo (0-255): los nichos sombríos conservan su ambiente pero legible.
    EXPOSURE_TARGET = 95
    EXPOSURE_TARGET_DARK = 75

    def __init__(self, output_dir: str = "output/temp_media", image_concurrency: int = 2,
                 upscaler: Optional[Upscaler] = None):
        load_env()
        self.upscaler = upscaler or Upscaler()
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._pexels_lock = threading.Lock()
        # El nivel anónimo de Pollinations responde 429 con peticiones simultáneas: una a la
        # vez. Con POLLINATIONS_TOKEN (registro gratuito) se permiten varias en paralelo.
        self._pollinations_gate = threading.Semaphore(max(1, image_concurrency) if self.pollinations_token else 1)

    @property
    def pixabay_key(self) -> Optional[str]:
        return os.getenv("PIXABAY_API_KEY") or None

    @property
    def pollinations_token(self) -> Optional[str]:
        return os.getenv("POLLINATIONS_TOKEN") or None

    @property
    def pexels_key(self) -> Optional[str]:
        return os.getenv("PEXELS_API_KEY") or None

    # ------------------------------------------------------------------ Imágenes IA
    def fetch_image(
        self,
        prompt: str,
        out_path: str,
        width: int = 1080,
        height: int = 1920,
        model: str = "flux",
        retries: int = 3,
        exposure_target: Optional[int] = None,
        upscale_model: Optional[str] = None,
    ) -> MediaResult:
        """Descarga una imagen IA. Reutiliza la existente si ya se generó (reanudación).

        Con Real-ESRGAN disponible se pide a 2/3 de la resolución (más rápido y con menos
        errores del servidor) y se mejora x2, lo que además limpia la compresión JPEG."""
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        if _valid_file(out_path, 5000):
            return MediaResult(out_path, "image")
        # El respaldo usa otro nombre: al reanudar el trabajo se vuelve a intentar la imagen real.
        root, ext = os.path.splitext(out_path)
        fallback_path = f"{root}.fallback{ext}"

        if width < height:
            aspect_tag = "vertical 9:16 composition"
        elif width > height:
            aspect_tag = "horizontal 16:9 widescreen composition"
        else:
            aspect_tag = "square composition"
        enhanced_prompt = f"{prompt.strip()[:600]}, {aspect_tag}, well lit subject, sharp focus, high detail, no text, no watermark"
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        # Semilla estable por prompt: reintentar el mismo trabajo da la misma imagen.
        base_seed = int(hashlib.md5(enhanced_prompt.encode("utf-8")).hexdigest()[:6], 16)

        # Se pide un 8% más de alto y se descarta la franja inferior, donde el nivel
        # gratuito de Pollinations estampa su marca de agua aunque se pida nologo.
        use_upscaler = self.upscaler.available
        request_w = int(round(width * (2 / 3 if use_upscaler else 1) / 16) * 16)
        request_h = int(round(height * (2 / 3 if use_upscaler else 1) * (1 + WATERMARK_BAND) / 16) * 16)
        last_error = ""
        for attempt in range(retries):
            seed = base_seed + attempt
            url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width={request_w}&height={request_h}&model={model}&nologo=true&seed={seed}"
            )
            wait = 3 * (attempt + 1) + random.random() * 2
            try:
                headers = dict(USER_AGENT)
                if self.pollinations_token:
                    headers["Authorization"] = f"Bearer {self.pollinations_token}"
                with self._pollinations_gate:
                    response = requests.get(url, headers=headers, timeout=self.IMAGE_TIMEOUT)
                if response.status_code == 200 and len(response.content) > 5000:
                    tmp = out_path + ".part"
                    with open(tmp, "wb") as file:
                        file.write(response.content)
                    upscaled = out_path + ".up.png"
                    source = tmp
                    if use_upscaler:
                        if upscale_model:
                            self.upscaler.model = upscale_model
                        source = self.upscaler.upscale(tmp, upscaled) or tmp
                    self._normalize_image(source, width, height, crop_bottom=WATERMARK_BAND,
                                          exposure_target=exposure_target or self.EXPOSURE_TARGET, save_to=tmp)
                    if os.path.exists(upscaled):
                        os.remove(upscaled)
                    os.replace(tmp, out_path)
                    return MediaResult(out_path, "image")
                last_error = f"HTTP {response.status_code}"
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "")
                    wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 15 * (attempt + 1)
            except requests.RequestException as error:
                last_error = type(error).__name__
            print(f"[MediaEngine] Imagen IA intento {attempt + 1}/{retries} falló ({last_error}).")
            if attempt < retries - 1:
                time.sleep(min(wait, 60))

        print(f"[MediaEngine] Imagen de respaldo para: {os.path.basename(out_path)}")
        self._generate_fallback_image(fallback_path, prompt, width, height)
        return MediaResult(fallback_path, "image", is_fallback=True, note=f"Pollinations no respondió ({last_error})")

    # ------------------------------------------------------------------ Fotos de stock
    def fetch_photo(self, query: str, out_path: str, width: int = 1080, height: int = 1920,
                    exposure_target: Optional[int] = None, alt_must_match: Optional[re.Pattern] = None) -> Optional[MediaResult]:
        """Foto real de Pexels (misma clave que los videos). Respaldo antes del degradado.
        Con alt_must_match solo vale una foto cuya descripción (alt) lo cumpla, p. ej. que sea de Colombia."""
        if _valid_file(out_path, 5000):
            return MediaResult(out_path, "image")
        if not self.pexels_key or not query.strip():
            return None
        orientation = "portrait" if width < height else ("landscape" if width > height else "square")
        try:
            res = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": self.pexels_key},
                               params={"query": _stock_query(query), "orientation": orientation,
                                       "per_page": 40 if alt_must_match else 10},
                               timeout=self.PEXELS_TIMEOUT)
            photos = res.json().get("photos", []) if res.status_code == 200 else []
        except (requests.RequestException, ValueError):
            return None
        for photo in photos:  # orden de relevancia del buscador
            if alt_must_match and not alt_must_match.search(photo.get("alt") or ""):
                continue
            src = photo.get("src") or {}
            url = src.get("large2x") or src.get("original")
            tmp = out_path + ".part.jpg"
            if url and self._download(url, tmp):
                self._normalize_image(tmp, width, height,
                                      exposure_target=exposure_target or self.EXPOSURE_TARGET, save_to=out_path)
                os.remove(tmp)
                return MediaResult(out_path, "image", note=f"Foto de Pexels ({photo.get('photographer', '')})")
        return None

    def fetch_pixabay_video(self, query: str, out_path: str, width: int = 1080, height: int = 1920,
                            exclude_ids: Optional[Set] = None) -> Optional[MediaResult]:
        """Segundo banco de video gratuito (clave gratis en pixabay.com/api/docs)."""
        if _valid_file(out_path, 50_000):
            return MediaResult(out_path, "video")
        if not self.pixabay_key:
            return None
        exclude_ids = exclude_ids if exclude_ids is not None else set()
        try:
            res = requests.get("https://pixabay.com/api/videos/", params={
                "key": self.pixabay_key, "q": _stock_query(query), "per_page": 15, "safesearch": "true",
            }, timeout=self.PEXELS_TIMEOUT)
            hits = res.json().get("hits", []) if res.status_code == 200 else []
        except (requests.RequestException, ValueError):
            return None
        for hit in hits:  # orden de relevancia del buscador
            key = ("pixabay", hit.get("id"))
            with self._pexels_lock:
                if key in exclude_ids:
                    continue
                exclude_ids.add(key)
            files = [
                {"link": v.get("url"), "width": v.get("width"), "height": v.get("height"), "file_type": "video/mp4"}
                for v in (hit.get("videos") or {}).values() if v.get("url")
            ]
            file_info = _best_video_file(files, width, height)
            if file_info and self._download(file_info["link"], out_path):
                print(f"[MediaEngine] Pixabay '{query}' -> clip {hit.get('id')}")
                return MediaResult(out_path, "video")
        return None

    def generate_image_ai(self, prompt: str, output_filename: str, width: int = 1080, height: int = 1920,
                          model: str = "flux", retries: int = 3) -> str:
        """Compatibilidad: devuelve solo la ruta."""
        return self.fetch_image(prompt, os.path.join(self.output_dir, output_filename), width, height, model, retries).path

    # ------------------------------------------------------------------ Pexels
    def fetch_video(
        self,
        query: str,
        out_path: str,
        width: int = 1080,
        height: int = 1920,
        min_duration: float = 0.0,
        exclude_ids: Optional[Set[int]] = None,
    ) -> Optional[MediaResult]:
        """Busca y descarga un clip de Pexels que no se haya usado ya en este video."""
        if _valid_file(out_path, 50_000):
            return MediaResult(out_path, "video")
        if not self.pexels_key:
            return None
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        exclude_ids = exclude_ids if exclude_ids is not None else set()

        orientation = "portrait" if width < height else ("landscape" if width > height else "square")
        words = [w.strip(".,;:\"'!?()[]{}").lower() for w in query.split()]
        keywords = [w for w in words if w and w not in PEXELS_STOPWORDS and len(w) > 2]
        final_query = " ".join(keywords[:3]) or "nature"
        attempts = [(final_query, orientation), (final_query, None)]
        if len(keywords) > 1:
            attempts.append((keywords[0], orientation))

        headers = {"Authorization": self.pexels_key}
        for q, orient in attempts:
            params = {"query": q, "per_page": 15}
            if orient:
                params["orientation"] = orient
            try:
                res = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=self.PEXELS_TIMEOUT)
            except requests.RequestException as error:
                print(f"[MediaEngine] Pexels no respondió para '{q}': {type(error).__name__}")
                continue
            if res.status_code in (401, 403):
                print("[MediaEngine] PEXELS_API_KEY inválida.")
                return None
            if res.status_code != 200:
                continue

            videos = res.json().get("videos", [])
            # Preferir clips que cubran la escena completa, sin repetir los ya usados.
            candidates = sorted(videos, key=lambda v: v.get("duration", 0) < min_duration)  # sorted es estable: conserva la relevancia de Pexels
            for video in candidates:
                with self._pexels_lock:
                    if video.get("id") in exclude_ids:
                        continue
                    exclude_ids.add(video.get("id"))
                file_info = _best_video_file(video.get("video_files", []), width, height)
                if not file_info:
                    continue
                if self._download(file_info["link"], out_path):
                    print(f"[MediaEngine] Pexels '{q}' -> clip {video.get('id')} ({file_info.get('width')}x{file_info.get('height')})")
                    return MediaResult(out_path, "video")
        return None

    def fetch_stock_video(self, query: str, output_filename: str, width: int = 1080, height: int = 1920,
                          min_duration: int = 5) -> Optional[str]:
        """Compatibilidad: devuelve solo la ruta o None."""
        result = self.fetch_video(query, os.path.join(self.output_dir, output_filename), width, height, min_duration)
        return result.path if result else None

    def _download(self, url: str, out_path: str) -> bool:
        tmp = out_path + ".part"
        try:
            with requests.get(url, stream=True, timeout=60, headers=USER_AGENT) as response:
                if response.status_code != 200:
                    return False
                with open(tmp, "wb") as file:
                    for chunk in response.iter_content(chunk_size=1 << 16):
                        file.write(chunk)
            os.replace(tmp, out_path)
            return True
        except (requests.RequestException, OSError) as error:
            print(f"[MediaEngine] Descarga fallida: {type(error).__name__}")
            if os.path.exists(tmp):
                os.remove(tmp)
            return False

    # ------------------------------------------------------------------ Utilidades
    def _normalize_image(self, path: str, target_w: int, target_h: int, crop_bottom: float = 0.0,
                         exposure_target: Optional[int] = None, save_to: Optional[str] = None):
        """Recorta al centro, escala a la resolución exacta del video y corrige la exposición."""
        with Image.open(path) as img:
            img = img.convert("RGB")
            if exposure_target:
                img = correct_exposure(img, exposure_target)
            if crop_bottom > 0:
                img = img.crop((0, 0, img.width, int(img.height / (1 + crop_bottom))))
            img_ratio = img.width / img.height
            target_ratio = target_w / target_h
            if abs(img_ratio - target_ratio) > 0.01:
                if img_ratio > target_ratio:
                    new_w = int(img.height * target_ratio)
                    left = (img.width - new_w) // 2
                    img = img.crop((left, 0, left + new_w, img.height))
                else:
                    new_h = int(img.width / target_ratio)
                    top = (img.height - new_h) // 2
                    img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            img.save(save_to or path, "JPEG", quality=95)

    def _generate_fallback_image(self, out_path: str, prompt: str = "", w: int = 1080, h: int = 1920) -> str:
        """Fondo abstracto con color derivado del prompt, para que escenas distintas no sean idénticas."""
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        rnd = random.Random(prompt)
        base = [rnd.randint(10, 60) for _ in range(3)]
        accent = [rnd.randint(60, 180) for _ in range(3)]
        small = Image.new("RGB", (max(1, w // 8), max(1, h // 8)))
        draw = ImageDraw.Draw(small)
        sw, sh = small.size
        for y in range(sh):
            t = y / sh
            draw.line([(0, y), (sw, y)], fill=tuple(int(b + (a - b) * t * 0.6) for b, a in zip(base, accent)))
        for _ in range(6):
            cx, cy, r = rnd.randint(0, sw), rnd.randint(0, sh), rnd.randint(sw // 6, sw // 2)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tuple(min(255, a + rnd.randint(0, 40)) for a in accent))
        img = small.filter(ImageFilter.GaussianBlur(radius=max(4, sw // 10))).resize((w, h), Image.Resampling.BICUBIC)
        img.save(out_path, "JPEG", quality=92)
        return out_path


MIN_GAMMA = 0.6


def mean_luma(img: Image.Image) -> float:
    return ImageStat.Stat(img.convert("L").resize((96, 170))).mean[0]


def exposure_gamma(img: Image.Image, target: float) -> float:
    """Gamma (<1 aclara) que llevaría la luminancia media al objetivo, sin estirar el rango."""
    histogram = img.convert("L").histogram()
    total = sum(histogram)

    def mean_for(gamma: float) -> float:
        return sum(255 * ((v / 255) ** gamma) * count for v, count in enumerate(histogram)) / total

    if mean_for(1.0) >= target:
        return 1.0
    if mean_for(MIN_GAMMA) < target:
        return MIN_GAMMA
    lo_g, hi_g = MIN_GAMMA, 1.0
    for _ in range(20):
        mid = (lo_g + hi_g) / 2
        if mean_for(mid) < target:
            hi_g = mid
        else:
            lo_g = mid
    return (lo_g + hi_g) / 2


def correct_exposure(img: Image.Image, target: float) -> Image.Image:
    """Si la imagen es más oscura que el objetivo: estira el rango tonal (percentiles 0.5-99.5)
    y aplica una curva gamma que sube los medios tonos sin quemar las luces."""
    current = mean_luma(img)
    if current >= target:
        return img
    histogram = img.convert("L").histogram()
    total = sum(histogram)
    low = _percentile_index(histogram, total * 0.005)
    high = 255 - _percentile_index(histogram[::-1], total * 0.005)
    high = max(high, low + 32)

    def lut_mean(lut) -> float:
        return sum(lut[v] * count for v, count in enumerate(histogram)) / total

    def solve(lo: int, hi: int):
        """Gamma (búsqueda binaria) que lleva la media al objetivo con el rango [lo, hi].
        El límite inferior (MIN_GAMMA) evita lavar la imagen cuando es de noche de verdad."""
        def build(gamma: float):
            return [int(round(255 * (min(max((v - lo) / (hi - lo), 0.0), 1.0) ** gamma))) for v in range(256)]
        lo_g, hi_g = MIN_GAMMA, 1.0
        if lut_mean(build(lo_g)) < target:
            gamma = lo_g
        else:
            for _ in range(20):
                mid = (lo_g + hi_g) / 2
                if lut_mean(build(mid)) < target:
                    hi_g = mid
                else:
                    lo_g = mid
            gamma = (lo_g + hi_g) / 2
        lut = build(gamma)
        return lut, gamma, lut_mean(lut)

    # Dos candidatos: estirar el rango + gamma, o solo gamma. Estirar lleva el negro a 0 y
    # puede oscurecer una penumbra suave; se elige el que más se acerque al objetivo y nunca
    # uno más oscuro que el original.
    candidates = [solve(low, high), solve(0, 255)]
    candidates = [c for c in candidates if c[2] >= current] or [(list(range(256)), 1.0, current)]
    lut, gamma, _ = min(candidates, key=lambda c: abs(c[2] - target))
    if gamma >= 0.999 and lut == list(range(256)):
        return img
    corrected = img.point(lut * 3)
    # Aclarar las sombras resta saturación; se recupera en proporción a la corrección.
    return ImageEnhance.Color(corrected).enhance(1.0 + (1.0 - gamma) * 0.5)


def _percentile_index(histogram, threshold: float) -> int:
    running = 0
    for index, count in enumerate(histogram):
        running += count
        if running > threshold:
            return index
    return 0


def _stock_query(query: str) -> str:
    words = [w.strip(".,;:!?()[]{}'\"").lower() for w in query.split()]
    keywords = [w for w in words if w and w not in PEXELS_STOPWORDS and len(w) > 2]
    return " ".join(keywords[:3]) or "nature"


def _valid_file(path: str, min_size: int) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > min_size


def _best_video_file(files, width: int, height: int):
    """Elige el archivo más pequeño que cubra la resolución objetivo (evita bajar 4K innecesario)."""
    usable = [f for f in files if f.get("link") and f.get("width") and f.get("height") and f.get("file_type", "video/mp4") == "video/mp4"]
    if not usable:
        return None
    target_short = min(width, height)
    covering = [f for f in usable if min(f["width"], f["height"]) >= target_short * 0.9]
    if covering:
        return min(covering, key=lambda f: f["width"] * f["height"])
    return max(usable, key=lambda f: f["width"] * f["height"])


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_HEADERS = {"User-Agent": "AIReelFactory/2.0 (videos documentales; uso personal)"}
# Licencias que permiten reutilizar con atribución, también con fines comerciales.
COMMONS_OK_LICENSES = ("public domain", "cc0", "cc by", "cc-by", "pd")
_commons_lock = threading.Lock()


def fetch_commons_image(engine: "MediaEngine", query: str, out_path: str, width: int = 1080, height: int = 1920,
                        exposure_target: Optional[int] = None, exclude: Optional[Set] = None) -> Optional[MediaResult]:
    """Imagen de archivo de Wikimedia Commons con licencia libre. La nota lleva la atribución."""
    if _valid_file(out_path, 5000):
        return MediaResult(out_path, "image")
    exclude = exclude if exclude is not None else set()
    try:
        data = requests.get(COMMONS_API, params={
            "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
            "gsrlimit": 12, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": max(width, height),
            "format": "json",
        }, headers=COMMONS_HEADERS, timeout=20).json()
    except (requests.RequestException, ValueError):
        return None
    pages = sorted((data.get("query", {}).get("pages", {}) or {}).values(), key=lambda p: p.get("index", 99))
    for page in pages:
        result = _take_commons_page(engine, page, out_path, width, height, exposure_target, exclude)
        if result:
            return result
    return None


def fetch_wikipedia_lead_image(engine: "MediaEngine", article_url: str, out_path: str, width: int = 1080,
                               height: int = 1350, exposure_target: Optional[int] = None) -> Optional[MediaResult]:
    """La imagen principal del artículo de Wikipedia (p. ej. la foto real del suceso), solo si
    está en Commons con licencia libre: las imágenes locales de Wikipedia suelen ser de uso legítimo."""
    if _valid_file(out_path, 5000):
        return MediaResult(out_path, "image")
    match = re.match(r"https?://(\w+)\.wikipedia\.org/wiki/(.+)", article_url or "")
    if not match:
        return None
    lang, title = match.group(1), urllib.parse.unquote(match.group(2))
    try:
        pages = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={
            "action": "query", "titles": title, "prop": "pageimages", "piprop": "name", "format": "json",
        }, headers=COMMONS_HEADERS, timeout=20).json().get("query", {}).get("pages", {})
        image = next((p.get("pageimage") for p in pages.values() if p.get("pageimage")), None)
        if not image:
            return None
        data = requests.get(COMMONS_API, params={
            "action": "query", "titles": f"File:{image}", "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime", "iiurlwidth": max(width, height), "format": "json",
        }, headers=COMMONS_HEADERS, timeout=20).json()
    except (requests.RequestException, ValueError):
        return None
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        result = _take_commons_page(engine, page, out_path, width, height, exposure_target, set())
        if result:
            return result
    return None


def _take_commons_page(engine: "MediaEngine", page: dict, out_path: str, width: int, height: int,
                       exposure_target: Optional[int], exclude: Set) -> Optional[MediaResult]:
    """Descarga un archivo de Commons si su licencia, tamaño y tipo sirven. La nota lleva la atribución."""
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    license_name = (meta.get("LicenseShortName", {}) or {}).get("value", "")
    if not any(ok in license_name.lower() for ok in COMMONS_OK_LICENSES):
        return None
    if min(info.get("width", 0), info.get("height", 0)) < 600 or page.get("title") in exclude:
        return None
    # Fotos y pinturas (JPEG); los mapas, diagramas y escudos suelen ser PNG/SVG y no sirven en video.
    if info.get("mime") != "image/jpeg" or re.search(r"map|mapa|diagram|plan|coat of arms|escudo|flag|bandera",
                                                      page.get("title", ""), re.IGNORECASE):
        return None
    url = info.get("thumburl") or info.get("url")
    # Se reserva la imagen antes de descargarla: dos escenas en paralelo no toman la misma.
    with _commons_lock:
        if page.get("title") in exclude:
            return None
        exclude.add(page.get("title"))
    tmp = out_path + ".part.jpg"
    if not url or not engine._download(url, tmp):
        return None
    try:
        engine._normalize_image(tmp, width, height, exposure_target=exposure_target or engine.EXPOSURE_TARGET, save_to=out_path)
    except OSError:
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    artist = re.sub(r"<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value", "")).strip() or "Autor desconocido"
    title = page.get("title", "").replace("File:", "")
    return MediaResult(out_path, "image", note=f"{title} · {artist} · {license_name} · Wikimedia Commons")
