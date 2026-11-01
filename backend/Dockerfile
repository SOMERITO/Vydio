FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DENO_INSTALL=/usr/local

# FFmpeg muxes separate audio/video streams. Deno runs the JavaScript
# challenge solvers required by yt-dlp for current YouTube extraction.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && curl -fsSL https://deno.land/install.sh -o /tmp/install-deno.sh \
    && sh /tmp/install-deno.sh \
    && deno --version \
    && ffmpeg -version | head -n 1 \
    && rm -f /tmp/install-deno.sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"

COPY backend/app.py ./app.py

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
