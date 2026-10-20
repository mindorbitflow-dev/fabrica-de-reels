"""Publicación en Instagram, Facebook y YouTube: reels (video) y posts de imagen.

Cada intento queda anotado en el publicacion.json del video (clave "publicado"), así un
reintento solo vuelve a subir a las redes que fallaron y nunca duplica una publicación.
"""

import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.core.job_manager import write_json_atomic

PLATFORMS = ("instagram", "facebook", "youtube")
GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
YOUTUBE_SECRET = "config/youtube_client_secret.json"
YOUTUBE_TOKEN = "config/youtube_token.json"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
INSTAGRAM_WAIT_SECONDS = 600


class PublishError(RuntimeError):
    pass


# ------------------------------------------------------------------ Estado y orquestación
def connected_platforms() -> List[str]:
    connected = []
    if os.getenv("META_PAGE_TOKEN") and os.getenv("INSTAGRAM_USER_ID"):
        connected.append("instagram")
    if os.getenv("META_PAGE_TOKEN") and os.getenv("META_PAGE_ID"):
        connected.append("facebook")
    if os.path.exists(YOUTUBE_TOKEN):
        connected.append("youtube")
    return connected


def auto_publish_blocker(package_path: str) -> Optional[str]:
    """Motivo para no publicar sin revisión: un video que seguro no concuerda con su tema."""
    package = _load(package_path)
    if package.get("script_source") == "offline":
        return "el guion es genérico (la IA no respondió)"
    if package.get("image_fallback"):
        return "la imagen es de respaldo, no del tema"
    manifest_path = os.path.join(os.path.dirname(package_path), "manifest.json")
    if os.path.exists(manifest_path):
        warnings = _load(manifest_path).get("warnings", [])
        if any("imagen de respaldo" in w.get("message", "") for w in warnings):
            return "hay escenas con imagen de respaldo en lugar de una imagen del tema"
    return None


def enabled_platforms(settings: Dict[str, Any], kind: str = "reel") -> List[str]:
    """Redes elegidas en config.yaml que además tienen la cuenta conectada. YouTube no admite posts de imagen."""
    connected = [p for p in connected_platforms() if kind != "post" or p != "youtube"]
    return [p for p in settings.get("platforms", PLATFORMS) if p in connected]


def auto_publish(package_path: str, settings: Dict[str, Any]) -> Tuple[Dict[str, Dict], Optional[str]]:
    """Publica al terminar un video si está activado. Devuelve (resultados, motivo si se omitió)."""
    platforms = enabled_platforms(settings, _load(package_path).get("type", "reel"))
    if not settings.get("auto") or not platforms:
        return {}, None
    blocker = auto_publish_blocker(package_path)
    if blocker:
        return {}, f"No se publicó automáticamente: {blocker}. Revísalo y usa «Publicar ahora» si te sirve."
    return publish_package(package_path, platforms, settings.get("youtube_privacy", "public")), None


def publish_package(package_path: str, platforms: List[str], youtube_privacy: str = "public") -> Dict[str, Dict]:
    package = _load(package_path)
    done = package.setdefault("publicado", {})
    is_post = package.get("type") == "post"
    for platform in platforms:
        if done.get(platform, {}).get("ok") or (is_post and platform == "youtube"):
            continue
        try:
            if is_post and platform == "instagram":
                url = publish_instagram_image(package)
            elif is_post and platform == "facebook":
                url = publish_facebook_photo(package)
            elif platform == "instagram":
                url = publish_instagram(package)
            elif platform == "facebook":
                url = publish_facebook(package)
            elif platform == "youtube":
                url = publish_youtube(package, youtube_privacy)
            else:
                raise PublishError(f"red desconocida: {platform}")
            done[platform] = {"ok": True, "url": url, "at": _now()}
        except Exception as error:
            done[platform] = {"ok": False, "error": str(error)[:400], "at": _now()}
        # Se guarda tras cada red: si algo se corta después, lo ya publicado no se repite.
        write_json_atomic(package_path, package)
    return done


def post_text(package: Dict[str, Any], limit: int) -> str:
    """Texto + hashtags + fuentes y créditos (las imágenes CC BY exigen atribución)."""
    text = "\n\n".join(t for t in (package.get("caption_completo", ""), package.get("fuentes_y_creditos", "")) if t)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ------------------------------------------------------------------ Meta (Instagram + Facebook)
def meta_pages(app_id: str, app_secret: str, user_token: str) -> List[Dict[str, Any]]:
    """Cambia el token corto del Explorador de la API Graph por uno largo y devuelve las páginas
    con su token de página (que no caduca) y la cuenta de Instagram vinculada."""
    long_lived = _graph("get", "oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                        client_secret=app_secret, fb_exchange_token=user_token)["access_token"]
    pages = _graph("get", "me/accounts", access_token=long_lived,
                   fields="id,name,access_token,instagram_business_account{id,username}").get("data", [])
    return [{"id": p["id"], "name": p.get("name", p["id"]), "token": p["access_token"],
             "instagram_id": (p.get("instagram_business_account") or {}).get("id", ""),
             "instagram_username": (p.get("instagram_business_account") or {}).get("username", "")}
            for p in pages]


def publish_instagram(package: Dict[str, Any]) -> str:
    token, ig_user = os.environ["META_PAGE_TOKEN"], os.environ["INSTAGRAM_USER_ID"]
    video = _media_path(package, "video")
    container = _graph("post", f"{ig_user}/media", access_token=token, media_type="REELS",
                       upload_type="resumable", share_to_feed="true", caption=post_text(package, 2200))
    _rupload(container["uri"], video, token)
    return _publish_instagram_container(ig_user, container["id"], token)


def publish_instagram_image(package: Dict[str, Any]) -> str:
    """Instagram solo acepta imágenes desde una URL pública: se aloja como foto NO publicada
    de la página de Facebook (no aparece en la página) y se le pasa esa URL."""
    token, ig_user, page = os.environ["META_PAGE_TOKEN"], os.environ["INSTAGRAM_USER_ID"], os.environ["META_PAGE_ID"]
    with open(_media_path(package, "image"), "rb") as image:
        hosted = _graph("post", f"{page}/photos", access_token=token, published="false", files={"source": image})
    sizes = _graph("get", hosted["id"], access_token=token, fields="images").get("images", [])
    if not sizes:
        raise PublishError("Facebook no devolvió la URL de la imagen alojada.")
    image_url = max(sizes, key=lambda i: i.get("width", 0))["source"]
    container = _graph("post", f"{ig_user}/media", access_token=token, image_url=image_url,
                       caption=post_text(package, 2200))
    return _publish_instagram_container(ig_user, container["id"], token)


def _publish_instagram_container(ig_user: str, container_id: str, token: str) -> str:
    deadline = time.time() + INSTAGRAM_WAIT_SECONDS
    while True:
        status = _graph("get", container_id, access_token=token, fields="status_code,status").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED") or time.time() > deadline:
            raise PublishError(f"Instagram no procesó el contenido (estado {status}).")
        time.sleep(5)
    media = _graph("post", f"{ig_user}/media_publish", access_token=token, creation_id=container_id)
    return _graph("get", media["id"], access_token=token, fields="permalink").get("permalink", "")


def publish_facebook_photo(package: Dict[str, Any]) -> str:
    token, page = os.environ["META_PAGE_TOKEN"], os.environ["META_PAGE_ID"]
    with open(_media_path(package, "image"), "rb") as image:
        photo = _graph("post", f"{page}/photos", access_token=token, message=post_text(package, 5000),
                       published="true", files={"source": image})
    return f"https://www.facebook.com/{photo.get('post_id') or photo['id']}"


def publish_facebook(package: Dict[str, Any]) -> str:
    token, page = os.environ["META_PAGE_TOKEN"], os.environ["META_PAGE_ID"]
    video = _media_path(package, "video")
    start = _graph("post", f"{page}/video_reels", access_token=token, upload_phase="start")
    _rupload(start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{GRAPH_VERSION}/{start['video_id']}",
             video, token)
    _graph("post", f"{page}/video_reels", access_token=token, upload_phase="finish", video_id=start["video_id"],
           video_state="PUBLISHED", description=post_text(package, 2200))
    return f"https://www.facebook.com/reel/{start['video_id']}"


def _rupload(url: str, video: str, token: str) -> None:
    with open(video, "rb") as file:
        response = requests.post(url, data=file, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(os.path.getsize(video)),
        })
    if response.status_code != 200:
        raise PublishError(f"Subida rechazada ({response.status_code}): {response.text[:200]}")


def _graph(method: str, path: str, files: Optional[Dict[str, Any]] = None, **params) -> Dict[str, Any]:
    send = requests.get if method == "get" else requests.post
    response = send(f"{GRAPH}/{path}", params=params if method == "get" else None,
                    data=params if method == "post" else None, files=files, timeout=120)
    data = response.json() if response.content else {}
    if response.status_code != 200 or "error" in data:
        message = (data.get("error") or {}).get("message", response.text[:200])
        raise PublishError(f"Meta: {message}")
    return data


# ------------------------------------------------------------------ YouTube
def connect_youtube() -> str:
    """Abre el navegador para autorizar el canal y guarda el token. Devuelve el nombre del canal."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(YOUTUBE_SECRET):
        raise PublishError(f"Falta {YOUTUBE_SECRET} (cliente OAuth de tipo «App de escritorio» de Google Cloud).")
    credentials = InstalledAppFlow.from_client_secrets_file(YOUTUBE_SECRET, YOUTUBE_SCOPES).run_local_server(port=0)
    with open(YOUTUBE_TOKEN, "w", encoding="utf-8") as file:
        file.write(credentials.to_json())
    channels = _youtube().channels().list(part="snippet", mine=True).execute().get("items", [])
    return channels[0]["snippet"]["title"] if channels else "canal sin nombre"


def publish_youtube(package: Dict[str, Any], privacy: str = "public") -> str:
    from googleapiclient.http import MediaFileUpload
    title = (package.get("title") or "Video").replace("<", "").replace(">", "")[:100]
    body = {
        "snippet": {
            "title": title,
            "description": post_text(package, 5000).replace("<", "").replace(">", ""),
            "tags": [h.lstrip("#") for h in package.get("hashtags", [])][:15],
            "categoryId": "27",  # Educación
        },
        # Imágenes y voz generadas por IA: YouTube pide declararlo cuando el contenido parece real.
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    request = _youtube().videos().insert(part="snippet,status", body=body,
                                         media_body=MediaFileUpload(_media_path(package, "video"), chunksize=-1, resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    return f"https://youtube.com/shorts/{response['id']}"


def _youtube():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(YOUTUBE_TOKEN, YOUTUBE_SCOPES)
    if not credentials.valid:
        if not credentials.refresh_token:
            raise PublishError("La conexión con YouTube caducó: vuelve a conectar el canal en Ajustes.")
        credentials.refresh(Request())
        with open(YOUTUBE_TOKEN, "w", encoding="utf-8") as file:
            file.write(credentials.to_json())
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


# ------------------------------------------------------------------ Utilidades
def _media_path(package: Dict[str, Any], key: str) -> str:
    path = package.get(key, "")
    if not os.path.exists(path):
        raise PublishError(f"El archivo ({key}) ya no existe.")
    return path


def _load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")
