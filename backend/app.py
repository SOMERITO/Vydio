from __future__ import annotations

import ipaddress
import logging
import mimetypes
import os
import re
from importlib.metadata import PackageNotFoundError, version as package_version
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yt_dlp
from gallery_dl import config as gallery_config
from gallery_dl import job as gallery_job
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

APP_VERSION = "0.3.0"
ROOT = Path(os.getenv("VYDIO_TEMP_DIR", "/tmp/vydio")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "512")) * 1024 * 1024
MAX_DURATION = int(os.getenv("MAX_DURATION_SECONDS", "2700"))
MAX_ACTIVE_DOWNLOADS = int(os.getenv("MAX_ACTIVE_DOWNLOADS", "2"))
JOB_TTL = int(os.getenv("JOB_TTL_SECONDS", "1800"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "12"))
MAX_GALLERY_IMAGES = int(os.getenv("MAX_GALLERY_IMAGES", "80"))
MAX_IMAGE_SIZE = int(os.getenv("MAX_IMAGE_SIZE_MB", "25")) * 1024 * 1024
MAX_GALLERY_SIZE = min(MAX_FILE_SIZE, int(os.getenv("MAX_GALLERY_SIZE_MB", "512")) * 1024 * 1024)
TIKTOK_CDN_HOSTS = (
    "tiktokcdn.com", "tiktokcdn-us.com", "tiktokv.com",
    "byteoversea.com", "ibytedtos.com", "muscdn.com",
    "byteicdn.com", "byteimg.com",
)

ALLOWED_HOSTS = {
    "youtube.com", "youtu.be",
    "instagram.com",
    "tiktok.com",
    "facebook.com", "fb.watch",
    "x.com", "twitter.com",
    "vimeo.com",
    "dailymotion.com",
    "reddit.com", "redd.it",
}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".avi"}
jobs: dict[str, dict[str, Any]] = {}
galleries: dict[str, dict[str, Any]] = {}
request_times: dict[str, list[float]] = {}
gallery_config_loaded = False
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=MAX_ACTIVE_DOWNLOADS, thread_name_prefix="vydio-download")
logger = logging.getLogger("vydio")


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    quality_mode: str = Field(default="auto", pattern="^(auto|manual)$")
    height: int | None = Field(default=None, ge=144, le=4320)


class GalleryDownloadRequest(BaseModel):
    gallery_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    image_indices: list[int] | None = Field(default=None, max_length=80)


class TikTokGalleryInfoJob(gallery_job.Job):
    """Extract TikTok photo URLs and metadata without downloading them."""

    def __init__(self, url: str):
        super().__init__(url)
        self.images: list[dict[str, Any]] = []
        self.post: dict[str, Any] = {}

    def handle_directory(self, kwdict: dict[str, Any]) -> None:
        self.post.update(kwdict)

    def handle_url(self, url: str, kwdict: dict[str, Any]) -> None:
        if kwdict.get("type") != "image":
            return
        if not isinstance(url, str) or not url.startswith("https://"):
            return
        self.images.append({
            "url": url,
            "width": int(kwdict.get("width") or 0),
            "height": int(kwdict.get("height") or 0),
            "extension": str(kwdict.get("extension") or "jpg").lower(),
            "index": int(kwdict.get("num") or len(self.images) + 1),
        })


def is_tiktok_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def is_tiktok_post_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return is_tiktok_url(url) and (
        bool(re.search(r"/(?:@[^/]+/)?(?:photo|video)/\d+", path))
        or "/share/" in path
        or host.startswith(("vm.", "vt."))
        or path.startswith("/t/")
    )


def validate_tiktok_cdn_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("La dirección de imagen de TikTok no es válida.")
    if not any(host == domain or host.endswith("." + domain) for domain in TIKTOK_CDN_HOSTS):
        raise ValueError("TikTok devolvió una dirección multimedia no reconocida.")
    return parsed.geturl()


def extract_tiktok_gallery_info(url: str) -> dict[str, Any] | None:
    global gallery_config_loaded
    if not is_tiktok_post_url(url):
        return None
    with lock:
        if not gallery_config_loaded:
            gallery_config.load()
            gallery_config_loaded = True

    extractor_job = TikTokGalleryInfoJob(url)
    status = extractor_job.run()
    images = extractor_job.images[:MAX_GALLERY_IMAGES]
    if not images:
        logger.info("TikTok extractor returned no photo items (status=%s)", status)
        return None

    safe_images = []
    for image in images:
        try:
            image["url"] = validate_tiktok_cdn_url(image["url"])
            safe_images.append(image)
        except ValueError:
            logger.warning("Skipped a TikTok media URL outside the approved CDN hosts")
    if not safe_images:
        return None

    post = extractor_job.post
    return {
        "title": str(post.get("title") or post.get("desc") or "Carrusel de TikTok")[:180],
        "uploader": str(post.get("user") or post.get("uploader") or "")[:100],
        "post_id": str(post.get("id") or "")[:80],
        "images": safe_images,
        "truncated": len(extractor_job.images) > MAX_GALLERY_IMAGES,
    }


def fetch_tiktok_image(url: str, max_bytes: int = MAX_IMAGE_SIZE) -> tuple[bytes, str]:
    safe_url = validate_tiktok_cdn_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    try:
        current_url = safe_url
        for _ in range(4):
            with requests.get(
                current_url,
                headers=headers,
                stream=True,
                timeout=(8, 25),
                allow_redirects=False,
            ) as response:
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("TikTok devolvió una redirección incompleta.")
                    current_url = validate_tiktok_cdn_url(urljoin(current_url, location))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].lower()
                if content_type not in {"image/jpeg", "image/png", "image/webp", "image/avif", "image/gif"}:
                    raise ValueError("TikTok no devolvió un formato de imagen compatible.")
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise ValueError("La imagen supera el límite de tamaño permitido.")
                chunks = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        raise ValueError("La imagen supera el límite de tamaño permitido.")
                if not chunks:
                    raise ValueError("La imagen está vacía o ya no está disponible.")
                return bytes(chunks), content_type
        raise ValueError("TikTok devolvió demasiadas redirecciones.")
    except requests.RequestException as exc:
        raise ValueError("No se pudo obtener una imagen de TikTok. Puede haber expirado o estar temporalmente restringida.") from exc


def validate_video_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Usa un enlace HTTPS público.")
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=422, detail="El enlace contiene un puerto no válido.") from None
    if parsed.username or parsed.password or port not in (None, 443):
        raise HTTPException(status_code=422, detail="El enlace contiene credenciales o un puerto no permitido.")
    host = parsed.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(status_code=422, detail="No se aceptan direcciones IP directas.")
    if not any(host == domain or host.endswith("." + domain) for domain in ALLOWED_HOSTS):
        raise HTTPException(
            status_code=422,
            detail="Por ahora se admiten enlaces de Instagram, TikTok, Facebook, YouTube, X, Vimeo, Dailymotion y Reddit.",
        )
    return parsed.geturl()


def rate_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.time()
    with lock:
        recent = [stamp for stamp in request_times.get(key, []) if now - stamp < 60]
        if len(recent) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Has alcanzado el límite temporal. Inténtalo de nuevo en un minuto.")
        recent.append(now)
        request_times[key] = recent


def cleanup_expired_jobs() -> None:
    cutoff = time.time() - JOB_TTL
    with lock:
        expired = [
            job_id for job_id, item in jobs.items()
            if item.get("updated_at", item.get("created_at", now())) < cutoff
            and item.get("status") in {"finished", "error"}
        ]
        for job_id in expired:
            item = jobs.pop(job_id, None)
            if item:
                shutil.rmtree(item.get("directory", ""), ignore_errors=True)
        expired_galleries = [
            gallery_id for gallery_id, item in galleries.items()
            if item.get("created_at", now()) < cutoff
        ]
        for gallery_id in expired_galleries:
            galleries.pop(gallery_id, None)


def now() -> float:
    return time.time()


def base_ydl_options(*, verbose: bool = False) -> dict[str, Any]:
    options = {
        "quiet": not verbose,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 1,
        "fragment_retries": 2,
        "extractor_retries": 1,
        "skip_unavailable_fragments": True,
        "nocheckcertificate": False,
        "geo_bypass": False,
        "restrictfilenames": True,
        "max_filesize": MAX_FILE_SIZE,
    }
    if verbose:
        options.update({
            "verbose": True,
            "no_warnings": False,
        })
    return options


def extract_video_info(url: str, *, verbose: bool = False) -> dict[str, Any]:
    safe_url = validate_video_url(url)
    options = base_ydl_options(verbose=verbose)
    options["skip_download"] = True
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(safe_url, download=False)
        if not info:
            raise ValueError("No se pudo obtener información del enlace.")
        if info.get("is_live"):
            raise ValueError("Las transmisiones en vivo no están disponibles.")
        if info.get("duration") and info["duration"] > MAX_DURATION:
            raise ValueError("El video supera el límite de duración permitido.")
        formats = info.get("formats") or []
        video_formats = [
            item for item in formats
            if item.get("vcodec") not in (None, "none") and item.get("height") and not item.get("has_drm")
        ]
        if not video_formats:
            raise ValueError("No se encontraron formatos de video descargables.")
        by_height: dict[int, dict[str, Any]] = {}
        for item in video_formats:
            height = int(item["height"])
            current = by_height.get(height)
            score = (item.get("tbr") or 0, item.get("filesize") or item.get("filesize_approx") or 0)
            if current is None:
                by_height[height] = item
            else:
                old_score = (current.get("tbr") or 0, current.get("filesize") or current.get("filesize_approx") or 0)
                if score > old_score:
                    by_height[height] = item
        available = []
        for height in sorted(by_height.keys()):
            item = by_height[height]
            size = item.get("filesize") or item.get("filesize_approx")
            available.append({
                "height": height,
                "label": f"{height}p",
                "ext": item.get("ext"),
                "fps": item.get("fps"),
                "filesize": size,
            })
        title = info.get("title") or "Video"
        return {
            "title": str(title)[:180],
            "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel"),
            "extractor": info.get("extractor_key") or info.get("extractor"),
            "thumbnail": info.get("thumbnail"),
            "available_heights": available,
            "best_height": max(item["height"] for item in available),
        }


def public_error(exc: Exception) -> str:
    message = str(exc).strip()
    lowered = message.lower()
    if "403" in lowered and any(term in lowered for term in ("http error", "forbidden", "status code")):
        return "La plataforma rechazó la petición del servidor (HTTP 403). Aunque el video sea público, YouTube puede bloquear solicitudes desde IP de centros de datos como Render. Necesitamos revisar el registro del error para confirmar si es ese caso; Deno y FFmpeg ya están disponibles."
    if "429" in lowered or "too many requests" in lowered:
        return "La plataforma está limitando temporalmente las solicitudes (HTTP 429). Espera un momento y vuelve a intentarlo; Vydio no repetirá la petición para evitar aumentar el bloqueo."
    if any(term in lowered for term in (
        "no supported javascript runtime", "javascript runtime", "js challenge",
        "challenge solver", "signature solving", "nsig extraction failed", "yt-dlp-ejs",
    )):
        return "Falta o no está disponible el motor JavaScript que YouTube necesita para resolver sus desafíos. Vydio está preparando una actualización del servidor; vuelve a intentarlo después del despliegue."
    if any(term in lowered for term in (
        "sign in to confirm", "confirm you're not a bot", "confirm you’re not a bot",
        "not a bot", "consent.youtube.com",
    )):
        return "YouTube está solicitando una verificación o inicio de sesión. Vydio no puede saltarse ese control; prueba más tarde o con otro enlace público."
    if any(term in lowered for term in (
        "not available in your country", "geo-restricted", "blocked in your country",
    )):
        return "Este video tiene una restricción geográfica y no está disponible para el servidor de Vydio."
    if any(term in lowered for term in (
        "video unavailable", "this video is unavailable", "private video",
        "video is private", "has been removed", "account associated with this video has been terminated",
    )):
        return "La plataforma indica que el video no está disponible públicamente, es privado o fue retirado."
    if any(term in lowered for term in ("login", "log in", "cookie", "private", "authentication", "checkpoint", "rate-limit", "too many requests")):
        return "La plataforma exige iniciar sesión, el contenido es privado o hay un límite temporal. Vydio no solicita credenciales ni intenta saltar esos controles."
    if "tiktok no permitió obtener las imágenes" in lowered:
        return message
    if "unsupported url" in lowered or "no suitable extractor" in lowered:
        return "Este enlace no es compatible actualmente."
    if "ffmpeg" in lowered:
        return "No se pudo combinar el audio y el video. El servidor necesita FFmpeg o la fuente ofrece un formato no compatible."
    if "maximum file size" in lowered or "filesize" in lowered:
        return "El archivo supera el límite de tamaño de Vydio."
    if "duration" in lowered:
        return "El video supera el límite de duración permitido."
    return "No se pudo procesar este enlace. Puede que la plataforma haya cambiado o que el video no esté disponible públicamente."


app = FastAPI(title="Vydio API", version=APP_VERSION, docs_url="/docs" if os.getenv("ENABLE_API_DOCS") == "true" else None)
origins = [item.strip() for item in os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500,https://somerito.github.io",
).split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        ejs_version = package_version("yt-dlp-ejs")
    except PackageNotFoundError:
        ejs_version = None
    return {
        "status": "ok",
        "service": "vydio-api",
        "version": APP_VERSION,
        "yt_dlp_version": yt_dlp.version.__version__,
        "yt_dlp_ejs_version": ejs_version,
        "capabilities": {
            "youtube_js_runtime": shutil.which("deno") is not None,
            "youtube_ejs_scripts": ejs_version is not None,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "tiktok_photo_galleries": True,
        },
    }


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    safe_url = validate_video_url(payload.url)
    try:
        logger.info(
            "Starting detailed analysis: host=%s yt_dlp=%s deno=%s",
            urlparse(safe_url).hostname,
            yt_dlp.version.__version__,
            bool(shutil.which("deno")),
        )
        if is_tiktok_url(safe_url):
            gallery_info = await run_in_threadpool(extract_tiktok_gallery_info, safe_url)
            if gallery_info:
                gallery_id = uuid.uuid4().hex
                with lock:
                    galleries[gallery_id] = {
                        **gallery_info,
                        "created_at": now(),
                        "source_url": safe_url,
                    }
                return {
                    "ok": True,
                    "media_type": "image_gallery",
                    "gallery_id": gallery_id,
                    "title": gallery_info["title"],
                    "uploader": gallery_info["uploader"],
                    "extractor": "TikTok · Carrusel",
                    "image_count": len(gallery_info["images"]),
                    "truncated": gallery_info["truncated"],
                    "images": [
                        {
                            "index": image["index"],
                            "width": image["width"],
                            "height": image["height"],
                            "preview_url": f"/api/galleries/{gallery_id}/images/{image['index']}",
                            "download_url": f"/api/galleries/{gallery_id}/images/{image['index']}/download",
                        }
                        for image in gallery_info["images"]
                    ],
                }
            if "/photo/" in urlparse(safe_url).path.lower():
                raise ValueError(
                    "TikTok no permitió obtener las imágenes de esta publicación. Comprueba que el carrusel sea público e inténtalo más tarde."
                )
        info = await run_in_threadpool(extract_video_info, safe_url, verbose=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Video analysis failed for host=%s", urlparse(safe_url).hostname)
        raise HTTPException(status_code=422, detail=public_error(exc)) from None
    return {"ok": True, **info}


def set_job(job_id: str, **changes: Any) -> None:
    with lock:
        if job_id in jobs:
            jobs[job_id].update(changes)
            jobs[job_id]["updated_at"] = now()


def run_download(job_id: str, url: str, quality_mode: str, height: int | None, directory: Path) -> None:
    set_job(job_id, status="analyzing", message="Comprobando formatos disponibles.", progress=0)
    try:
        info = extract_video_info(url)
        best_height = info["best_height"]
        selected_height = best_height if quality_mode == "auto" else height
        valid_heights = {item["height"] for item in info["available_heights"]}
        if selected_height not in valid_heights:
            raise ValueError("La resolución seleccionada no está disponible para este video.")
        fmt = "bestvideo+bestaudio/best" if quality_mode == "auto" else (
            f"bestvideo[height<={selected_height}]+bestaudio/best[height<={selected_height}]"
        )

        def progress_hook(data: dict[str, Any]) -> None:
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                received = data.get("downloaded_bytes") or 0
                percent = min(99, round(received * 100 / total)) if total else None
                set_job(
                    job_id,
                    status="downloading",
                    progress=percent,
                    message="Descargando y preparando el archivo.",
                    downloaded_bytes=received,
                    total_bytes=total,
                )
            elif data.get("status") == "finished":
                set_job(job_id, status="merging", progress=99, message="Combinando audio y video si es necesario.")

        def duration_filter(entry: dict[str, Any], *, incomplete: bool = False) -> str | None:
            duration = entry.get("duration")
            if duration and duration > MAX_DURATION:
                return f"El video supera el límite de {MAX_DURATION} segundos."
            return None

        options = base_ydl_options()
        options.update({
            "format": fmt,
            "outtmpl": str(directory / "%(title).120B [%(id)s].%(ext)s"),
            "merge_output_format": "mkv",
            "progress_hooks": [progress_hook],
            "match_filter": duration_filter,
            "noplaylist": True,
            "overwrites": False,
        })
        set_job(
            job_id,
            status="downloading",
            title=info["title"],
            selected_height=selected_height,
            best_height=best_height,
            message="Descargando la mejor calidad disponible." if quality_mode == "auto" else f"Descargando hasta {selected_height}p.",
            progress=0,
        )
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)

        candidates = [
            path for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.endswith(".part")
        ]
        if not candidates:
            raise RuntimeError("No se encontró el archivo final tras la descarga.")
        final_file = max(candidates, key=lambda path: path.stat().st_size)
        if final_file.stat().st_size > MAX_FILE_SIZE:
            final_file.unlink(missing_ok=True)
            raise RuntimeError("El archivo supera el límite de tamaño permitido.")
        set_job(
            job_id,
            status="finished",
            progress=100,
            message="Descarga preparada.",
            filename=final_file.name,
            file_size=final_file.stat().st_size,
            file_path=str(final_file),
            updated_at=now(),
        )
    except Exception as exc:
        logger.exception("Download job failed for job_id=%s", job_id)
        set_job(job_id, status="error", message=public_error(exc), error=True, updated_at=now())


@app.post("/api/download", status_code=202)
def create_download(payload: DownloadRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    safe_url = validate_video_url(payload.url)
    if payload.quality_mode == "manual" and payload.height is None:
        raise HTTPException(status_code=422, detail="Selecciona una resolución disponible.")
    cleanup_expired_jobs()
    with lock:
        active = sum(1 for item in jobs.values() if item.get("status") in {"queued", "analyzing", "downloading", "merging"})
        if active >= MAX_ACTIVE_DOWNLOADS:
            raise HTTPException(status_code=429, detail="Vydio está procesando otras descargas. Inténtalo más tarde.")
        job_id = uuid.uuid4().hex
        directory = ROOT / job_id
        directory.mkdir(parents=True, exist_ok=True)
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Descarga en cola.",
            "created_at": now(),
            "updated_at": now(),
            "directory": str(directory),
        }
    executor.submit(run_download, job_id, safe_url, payload.quality_mode, payload.height, directory)
    return {"ok": True, "job_id": job_id, "status": "queued"}




@app.get("/api/galleries/{gallery_id}/images/{image_index}")
def preview_gallery_image(gallery_id: str, image_index: int) -> Response:
    cleanup_expired_jobs()
    with lock:
        gallery = galleries.get(gallery_id)
        if not gallery:
            raise HTTPException(status_code=404, detail="La galería expiró. Analiza de nuevo el enlace.")
        image = next((item for item in gallery["images"] if item["index"] == image_index), None)
        if not image:
            raise HTTPException(status_code=404, detail="No se encontró esa imagen del carrusel.")
        image_url = image["url"]
    try:
        content, media_type = fetch_tiktok_image(image_url)
    except Exception as exc:
        logger.warning("TikTok gallery preview failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/galleries/{gallery_id}/images/{image_index}/download")
def download_gallery_image(gallery_id: str, image_index: int, request: Request) -> Response:
    rate_limit(request)
    cleanup_expired_jobs()
    with lock:
        gallery = galleries.get(gallery_id)
        if not gallery:
            raise HTTPException(status_code=404, detail="La galería expiró. Analiza de nuevo el enlace.")
        image = next((item for item in gallery["images"] if item["index"] == image_index), None)
        if not image:
            raise HTTPException(status_code=404, detail="No se encontró esa imagen del carrusel.")
        image_url = image["url"]
    try:
        content, media_type = fetch_tiktok_image(image_url)
    except Exception as exc:
        logger.warning("TikTok gallery image download failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None
    extension = mimetypes.guess_extension(media_type) or ("." + image.get("extension", "jpg"))
    filename = f"vydio_tiktok_{image_index:02d}{extension}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def run_gallery_download(job_id: str, gallery: dict[str, Any], selected: list[int], directory: Path) -> None:
    set_job(job_id, status="analyzing", progress=0, message="Preparando las imágenes seleccionadas.")
    zip_path = directory / "Vydio_TikTok_carrusel.zip"
    completed = 0
    total_bytes = 0
    failed = 0
    try:
        image_by_index = {item["index"]: item for item in gallery["images"]}
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for position, image_index in enumerate(selected, start=1):
                image = image_by_index[image_index]
                set_job(
                    job_id,
                    status="downloading",
                    progress=round((position - 1) * 100 / len(selected)),
                    message=f"Descargando imagen {position} de {len(selected)}…",
                )
                try:
                    content, media_type = fetch_tiktok_image(image["url"])
                    total_bytes += len(content)
                    if total_bytes > MAX_GALLERY_SIZE:
                        raise ValueError("El ZIP supera el límite total de tamaño permitido.")
                    extension = mimetypes.guess_extension(media_type) or ("." + image.get("extension", "jpg"))
                    archive.writestr(f"imagen_{image_index:02d}{extension}", content)
                    completed += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("Skipped TikTok gallery image %s in job %s: %s", image_index, job_id, exc)
                set_job(
                    job_id,
                    status="downloading",
                    progress=round(position * 100 / len(selected)),
                    message=f"Procesadas {position} de {len(selected)} imágenes…",
                )
        if completed == 0:
            raise RuntimeError("No se pudo descargar ninguna imagen del carrusel.")
        filename = f"Vydio_TikTok_{gallery.get('post_id') or 'carrusel'}.zip"
        set_job(
            job_id,
            status="finished",
            progress=100,
            message=(
                f"ZIP preparado con {completed} imágenes."
                + (f" No se pudieron obtener {failed}." if failed else "")
            ),
            filename=filename,
            file_size=zip_path.stat().st_size,
            file_path=str(zip_path),
            updated_at=now(),
        )
    except Exception as exc:
        logger.exception("TikTok gallery download failed for job_id=%s", job_id)
        set_job(job_id, status="error", message=str(exc), error=True, updated_at=now())


@app.post("/api/gallery-download", status_code=202)
def create_gallery_download(payload: GalleryDownloadRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    cleanup_expired_jobs()
    with lock:
        gallery = galleries.get(payload.gallery_id)
        if not gallery:
            raise HTTPException(status_code=404, detail="La galería expiró. Analiza de nuevo el enlace.")
        available = {item["index"] for item in gallery["images"]}
        selected = sorted(available) if payload.image_indices is None else payload.image_indices
        if not selected or len(selected) > MAX_GALLERY_IMAGES:
            raise HTTPException(status_code=422, detail="Selecciona al menos una imagen.")
        if len(set(selected)) != len(selected) or any(index not in available for index in selected):
            raise HTTPException(status_code=422, detail="La selección contiene imágenes no válidas.")
        active = sum(
            1 for item in jobs.values()
            if item.get("status") in {"queued", "analyzing", "downloading", "merging"}
        )
        if active >= MAX_ACTIVE_DOWNLOADS:
            raise HTTPException(status_code=429, detail="Vydio está procesando otras descargas. Inténtalo más tarde.")
        job_id = uuid.uuid4().hex
        directory = ROOT / job_id
        directory.mkdir(parents=True, exist_ok=True)
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Descarga del carrusel en cola.",
            "created_at": now(),
            "updated_at": now(),
            "directory": str(directory),
        }
        gallery_snapshot = {
            "post_id": gallery.get("post_id"),
            "images": [dict(item) for item in gallery["images"]],
        }
    executor.submit(run_gallery_download, job_id, gallery_snapshot, selected, directory)
    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    # Polling is frequent while a job runs; rate-limit job creation instead.
    cleanup_expired_jobs()
    with lock:
        item = jobs.get(job_id)
        if not item:
            raise HTTPException(status_code=404, detail="La tarea no existe o expiró.")
        return {
            key: item.get(key)
            for key in ("id", "status", "progress", "message", "title", "filename", "file_size", "selected_height", "best_height")
        } | {"download_ready": item.get("status") == "finished"}


@app.get("/api/jobs/{job_id}/file")
def download_file(job_id: str, request: Request) -> FileResponse:
    rate_limit(request)
    with lock:
        item = jobs.get(job_id)
        if not item or item.get("status") != "finished":
            raise HTTPException(status_code=404, detail="El archivo todavía no está disponible o la tarea expiró.")
        path = Path(item.get("file_path", "")).resolve()
        directory = Path(item.get("directory", "")).resolve()
        if directory not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="No se encontró el archivo.")
        filename = item.get("filename") or path.name
    return FileResponse(path, media_type="application/octet-stream", filename=filename)
