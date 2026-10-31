# Vydio

**Your videos. Your quality.**

Vydio is a multiplatform video-download interface prototype. Its default behavior is to request the highest available quality; manual mode lets the user request a lower resolution when the source provides it.

## Current status

This repository currently contains the responsive frontend and interaction scaffolding. It does **not** yet download or extract videos. A backend must be implemented and connected before the Analyze/Download flow can work.

## Run locally

Open `index.html` in a browser, or serve the directory with any static web server.

## Quality behavior

- **Automatic (default):** request the best available video and audio streams supported by the source.
- **Manual:** request the selected resolution, but only offer resolutions actually available.
- Never upscale or claim a resolution that the source does not provide.
- If video and audio are separate streams, the backend must mux them into a final file.

## Planned architecture

- Frontend: static HTML, CSS and JavaScript (GitHub Pages compatible).
- Backend: separate service for authorized source analysis, stream selection and file processing.
- Processing should respect each platform's terms, copyright, privacy settings and access controls. Do not bypass DRM, private content, login walls or technical restrictions.
- Never ask users for their social-media passwords or store platform session cookies.

## Deploy the frontend with GitHub Pages

1. Open **Settings → Pages** in this repository.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select branch `main` and folder `/(root)`.
4. Save. GitHub will publish the static frontend when deployment completes.

GitHub Pages only hosts static files; it cannot run the video-processing backend.

## Next development milestones

1. Connect a backend for sources that explicitly permit downloading.
2. Add source capability detection and return truthful format/quality options.
3. Add secure temporary storage, size/time limits and cleanup.
4. Add progress, cancellation, and robust errors.
5. Add automated tests and deployment checks.
