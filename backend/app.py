from __future__ import annotations

import ipaddress
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

APP_VERSION = "0.2.0"
ROOT = Path(os.getenv("VYDIO_TEMP_DIR", "/tmp/vydio")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "512")) * 1024 * 1024
MAX_DURATION = int(os.getenv("MAX_DURATION_SECONDS", "2700"))
MAX_ACTIVE_DOWNLOADS = int(os.getenv("MAX_ACTIVE_DOWNLOADS", "2"))
JOB_TTL = int(os.getenv("JOB_TTL_SECONDS", "1800"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "12"))

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
request_times: dict[str, list[float]] = {}
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=MAX_ACTIVE_DOWNLOADS, thread_name_prefix="vydio-download")


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    quality_mode: str = Field(default="auto", pattern="^(auto|manual)$")
    height: int | None = Field(default=None, ge=144, le=4320)


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


def now() -> float:
    return time.time()


def base_ydl_options() -> dict[str, Any]:
    return {
        "quiet": True,
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


def extract_video_info(url: str) -> dict[str, Any]:
    safe_url = validate_video_url(url)
    options = base_ydl_options()
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
    if any(term in lowered for term in ("login", "log in", "cookie", "private", "authentication", "checkpoint", "rate-limit", "too many requests")):
        return "La plataforma exige iniciar sesión, el contenido es privado o hay un límite temporal. Vydio no solicita credenciales ni intenta saltar esos controles."
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
def health() -> dict[str, str]:
    return {"status": "ok", "service": "vydio-api", "version": APP_VERSION}


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, Any]:
    rate_limit(request)
    safe_url = validate_video_url(payload.url)
    try:
        info = await run_in_threadpool(extract_video_info, safe_url)
    except HTTPException:
        raise
    except Exception as exc:
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
