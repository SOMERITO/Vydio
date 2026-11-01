# Vydio

**Your videos. Your quality.**

Vydio is a web frontend plus a separate API for analyzing supported public video links and preparing downloads. Automatic mode requests the best available quality; manual mode lets the user choose from resolutions actually returned by the source.

## Current implementation

- Installable Progressive Web App (PWA) with a web manifest, 192px/512px Android icons, maskable icon, standalone display mode and service-worker app-shell cache.
- On supported browsers, an **Instalar** button appears when the browser signals that Vydio can be installed. Otherwise use the browser menu → **Instalar aplicación** / **Añadir a pantalla de inicio**.
- Responsive static frontend (GitHub Pages compatible).
- API endpoint for analyzing supported links and listing available video heights.
- Health diagnostics report yt-dlp, yt-dlp-ejs, Deno and FFmpeg versions/availability.
- Detailed yt-dlp extractor diagnostics are written to Render application logs during video analysis.
- Clearer user-facing distinction between HTTP 403 (often data-center IP restrictions) and HTTP 429 rate limits.
- Download jobs with status polling and progress.
- Automatic quality selection or manual maximum-height selection.
- Temporary file storage with expiry cleanup, file-size/duration limits, request throttling and a small concurrent-download limit.
- No platform passwords, cookies, session tokens, private-content access, DRM circumvention or login-wall bypass.

The API uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) as an extraction library (minimum version 2026.10.26). The Docker image installs **Deno** as the JavaScript runtime and upgrades `yt-dlp[default,curl-cffi]` to the project's recommended **nightly** channel at build time. This includes the EJS challenge scripts needed for current YouTube extraction and lets extractor fixes arrive between stable releases. Supported-site coverage can change, and being listed does not guarantee that every URL will work. Some platforms block automated access or require authentication; Vydio intentionally does not request credentials or attempt to bypass those restrictions.

## Install on Android

1. Open the published Vydio site in Chrome using its HTTPS GitHub Pages URL.
2. Wait for the page to finish loading and for the service worker to register.
3. Open Chrome's three-dot menu and choose **Install app** (or **Install and create shortcut** → **Install**). If Chrome exposes the in-page **Instalar** action, it can be used instead.
4. If the old “No se puede instalar esta app” prompt remains, close it, reload the site after the Pages deployment finishes, and revisit the HTTPS URL. Chrome may need to refresh the manifest/installability data.

The installed PWA opens in a standalone app window, but video analysis/download still requires an internet connection and the Vydio API. The service worker caches only the web app shell; it does not cache API responses or media downloads.

## Run locally

Requirements: Python 3.12+, FFmpeg, and a current yt-dlp-supported environment.

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

In the repository root, edit `config.js`:

```js
window.VYDIO_API_BASE = "http://localhost:8000";
```

Serve the frontend from a local static server (for example, `python -m http.server 5500`) and open it in the browser.

## Deploy the API with Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FSOMERITO%2FVydio)

Or sign in to Render and choose **New → Blueprint**.
2. Connect `SOMERITO/Vydio` and let Render read the root `render.yaml`.
3. Create the `vydio-api` web service. The Dockerfile installs FFmpeg and the Python dependencies.
4. When Render gives the service URL, edit `config.js` and set `window.VYDIO_API_BASE` to that origin, e.g. `https://vydio-api.onrender.com`.
5. Commit the change. Keep the origin in `ALLOWED_ORIGINS` aligned with the GitHub Pages origin if you use a custom domain.
6. Publish the frontend using **Settings → Pages → Deploy from a branch → main → /(root)**.

The Render free instance may sleep when idle, so the first request after inactivity can take longer. Local temporary downloads are not permanent storage; completed files expire after the configured TTL and may be lost when the service restarts.

## API

- `GET /health` (also reports whether Deno and FFmpeg are available)
- `POST /api/analyze` with `{"url":"https://..." }`
- `POST /api/download` with `{"url":"https://...","quality_mode":"auto"}` or `{"url":"https://...","quality_mode":"manual","height":720}`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/file`

## Limits and responsible use

Defaults: 512 MiB maximum file size, 45 minutes maximum duration, two simultaneous jobs, 12 API requests per minute per client and 30-minute job/file expiry. Tune these environment variables for your deployment.

Only download media that you own or are authorized to save, and follow the source platform's terms and applicable law. Vydio only accepts HTTPS links from its configured platform allowlist. It does not bypass DRM, private accounts, paywalls, login requirements or access controls. The server is a public service once deployed; review resource limits and hosting costs before sharing it widely.


## TikTok photo carousels

Vydio uses `gallery-dl` to detect public TikTok photo-mode posts and extract each slide, while `yt-dlp` remains responsible for video downloads. The frontend previews carousel images through the API, supports individual image downloads, and can package selected images into a ZIP. The image extractor is limited to public post URLs and approved TikTok CDN hosts; private posts and login challenges are not bypassed.

Environment controls:
- `MAX_GALLERY_IMAGES` (default: 80)
- `MAX_IMAGE_SIZE_MB` (default: 25)
- `MAX_GALLERY_SIZE_MB` (default: 512)
