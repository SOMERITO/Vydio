const urlInput = document.querySelector("#video-url");
const analyzeButton = document.querySelector("#analyze-btn");
const pasteButton = document.querySelector("#paste-btn");
const feedback = document.querySelector("#feedback");
const manualQuality = document.querySelector("#manual-quality");
const qualitySelect = document.querySelector("#quality-select");
const modeButtons = [...document.querySelectorAll(".quality-option")];
const resultsPanel = document.querySelector("#analysis-results");
const titleEl = document.querySelector("#analysis-title");
const sourceEl = document.querySelector("#analysis-source");
const bestQualityEl = document.querySelector("#best-quality");
const downloadButton = document.querySelector("#download-btn");
const downloadLabel = document.querySelector("#download-label");
const progressPanel = document.querySelector("#download-progress");
const progressBar = document.querySelector("#progress-bar");
const progressMessage = document.querySelector("#progress-message");
const progressValue = document.querySelector("#progress-value");
const fileLink = document.querySelector("#file-link");
const apiIndicator = document.querySelector("#api-indicator");
const thumbnailWrap = document.querySelector("#analysis-preview");
const thumbnail = document.querySelector("#analysis-thumbnail");
const setupApiLink = document.querySelector("#setup-api-link");
const installAppButton = document.querySelector("#install-app-button");
const globalLoading = document.querySelector("#global-loading");
const downloadProgressTrack = document.querySelector("#download-progress .progress-track");
const galleryResults = document.querySelector("#gallery-results");
const galleryGrid = document.querySelector("#gallery-grid");
const gallerySelectAllButton = document.querySelector("#gallery-select-all");
const gallerySelectedCount = document.querySelector("#gallery-selected-count");
const galleryDownloadButton = document.querySelector("#gallery-download-btn");
let analyzedGallery = null;
let gallerySelected = new Set();
let activeDownloadKind = "video";

const API_BASE = String(window.VYDIO_API_BASE || "").trim().replace(/\/+$/, "");
let qualityMode = "auto";
let analyzedVideo = null;
let pollingTimer = null;
let activeJobId = null;

function apiUrl(path) {
  if (!API_BASE) throw new Error("El servidor de Vydio todavía no está conectado. Primero hay que desplegar la API y configurar config.js.");
  return API_BASE + path;
}

function setFeedback(message, kind = "info") {
  feedback.textContent = message;
  feedback.dataset.kind = kind;
}

function setGlobalLoading(active, progress = null) {
  if (!globalLoading) return;
  globalLoading.hidden = !active;
  if (!active) {
    globalLoading.dataset.mode = "indeterminate";
    globalLoading.style.removeProperty("--loading-progress");
    globalLoading.removeAttribute("aria-valuenow");
    return;
  }
  if (Number.isFinite(progress)) {
    const safeProgress = Math.max(0, Math.min(100, progress));
    globalLoading.dataset.mode = "determinate";
    globalLoading.style.setProperty("--loading-progress", safeProgress + "%");
    globalLoading.setAttribute("aria-valuenow", String(Math.round(safeProgress)));
  } else {
    globalLoading.dataset.mode = "indeterminate";
    globalLoading.style.removeProperty("--loading-progress");
    globalLoading.removeAttribute("aria-valuenow");
  }
}

function setBusy(button, busy, text) {
  button.disabled = busy;
  button.classList.toggle("is-loading", busy);
  button.setAttribute("aria-busy", String(busy));
  if (text) button.querySelector("span").textContent = text;
}

function setDownloadBusy(busy) {
  downloadButton.disabled = busy;
  downloadButton.classList.toggle("is-loading", busy);
  downloadButton.setAttribute("aria-busy", String(busy));
  if (busy) downloadLabel.textContent = "Preparando descarga…";
  else updateDownloadLabel();
}

function setGalleryBusy(busy) {
  if (!galleryDownloadButton) return;
  galleryDownloadButton.disabled = busy || gallerySelected.size === 0;
  galleryDownloadButton.classList.toggle("is-loading", busy);
  galleryDownloadButton.setAttribute("aria-busy", String(busy));
  const label = galleryDownloadButton.querySelector("span");
  if (label) label.textContent = busy ? "Preparando ZIP…" : "Descargar selección (ZIP)";
}

function updateGallerySelection() {
  if (!analyzedGallery) return;
  const total = analyzedGallery.images.length;
  const selected = gallerySelected.size;
  if (gallerySelectedCount) {
    gallerySelectedCount.textContent = selected + " de " + total + " imágenes seleccionadas";
  }
  if (gallerySelectAllButton) {
    gallerySelectAllButton.textContent = selected === total ? "Deseleccionar todas" : "Seleccionar todas";
  }
  setGalleryBusy(false);
}

function renderGallery(data) {
  if (!galleryGrid || !galleryResults) return;
  galleryGrid.replaceChildren();
  gallerySelected = new Set(data.images.map(image => image.index));
  const heading = document.querySelector("#gallery-title");
  const description = document.querySelector("#gallery-description");
  if (heading) heading.textContent = data.title || "Carrusel de TikTok";
  if (description) {
    const owner = data.uploader ? "@" + data.uploader + " · " : "";
    description.textContent = owner + data.image_count + " imágenes · resolución original disponible";
  }

  data.images.forEach(imageData => {
    const card = document.createElement("article");
    card.className = "gallery-image-card";

    const media = document.createElement("div");
    media.className = "gallery-image-media";
    const image = document.createElement("img");
    image.src = apiUrl(imageData.preview_url);
    image.alt = "Imagen " + imageData.index + " del carrusel";
    image.loading = "lazy";
    image.decoding = "async";
    image.referrerPolicy = "no-referrer";
    media.appendChild(image);

    const selection = document.createElement("label");
    selection.className = "gallery-select-control";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.setAttribute("aria-label", "Seleccionar imagen " + imageData.index);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) gallerySelected.add(imageData.index);
      else gallerySelected.delete(imageData.index);
      updateGallerySelection();
    });
    const checkmark = document.createElement("span");
    checkmark.setAttribute("aria-hidden", "true");
    selection.append(checkbox, checkmark);
    media.appendChild(selection);

    const number = document.createElement("span");
    number.className = "gallery-image-number";
    number.textContent = String(imageData.index).padStart(2, "0");
    media.appendChild(number);

    const details = document.createElement("div");
    details.className = "gallery-image-details";
    const name = document.createElement("strong");
    name.textContent = "Imagen " + imageData.index;
    const dimensions = document.createElement("small");
    dimensions.textContent = imageData.width && imageData.height
      ? imageData.width + " × " + imageData.height + " · Original"
      : "Máxima calidad disponible";
    details.append(name, dimensions);

    const individual = document.createElement("a");
    individual.className = "gallery-single-download";
    individual.href = apiUrl(imageData.download_url);
    individual.textContent = "Descargar";
    individual.setAttribute("download", "");
    individual.rel = "nofollow";
    individual.setAttribute("aria-label", "Descargar imagen " + imageData.index);
    details.appendChild(individual);

    card.append(media, details);
    galleryGrid.appendChild(card);
  });

  galleryResults.classList.remove("hidden");
  if (data.truncated) {
    setFeedback("Carrusel detectado. Por seguridad, se muestran las primeras " + data.images.length + " imágenes.", "warning");
  }
  updateGallerySelection();
}

gallerySelectAllButton?.addEventListener("click", () => {
  if (!analyzedGallery) return;
  if (gallerySelected.size === analyzedGallery.images.length) gallerySelected.clear();
  else gallerySelected = new Set(analyzedGallery.images.map(image => image.index));
  galleryGrid.querySelectorAll('input[type="checkbox"]').forEach((checkbox, position) => {
    checkbox.checked = gallerySelected.has(analyzedGallery.images[position].index);
  });
  updateGallerySelection();
});

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "Duración no disponible";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return minutes ? minutes + " min " + remainder + " s" : remainder + " s";
}

function setMode(mode) {
  qualityMode = mode;
  modeButtons.forEach(button => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
  manualQuality.classList.toggle("hidden", mode !== "manual");
  if (mode === "manual" && !analyzedVideo) {
    setFeedback("Primero analiza el enlace para consultar las resoluciones realmente disponibles.");
  } else if (analyzedVideo) {
    updateDownloadLabel();
    setFeedback(mode === "auto"
      ? "Automático seleccionado: se utilizará la mayor resolución disponible."
      : "Selecciona una de las resoluciones detectadas.");
  } else {
    setFeedback("Automático seleccionado: se priorizará la mejor calidad disponible.");
  }
}

function updateDownloadLabel() {
  if (!analyzedVideo) return;
  if (qualityMode === "auto") {
    downloadLabel.textContent = "Descargar máxima calidad";
    return;
  }
  const selected = qualitySelect.value;
  downloadLabel.textContent = selected ? "Descargar " + selected + "p" : "Elegir resolución";
}

modeButtons.forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));

pasteButton.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text) return setFeedback("El portapapeles está vacío.");
    urlInput.value = text.trim();
    setFeedback("Enlace pegado. Ahora puedes analizarlo.");
  } catch {
    setFeedback("No se pudo acceder al portapapeles. Pega el enlace directamente en el campo.");
  }
});

qualitySelect.addEventListener("change", updateDownloadLabel);

async function readApiResponse(response) {
  let body = {};
  try { body = await response.json(); } catch { /* API response may not be JSON */ }
  if (!response.ok) throw new Error(body.detail || "El servidor no pudo completar la solicitud.");
  return body;
}

async function analyzeVideo() {
  const value = urlInput.value.trim();
  if (!value) {
    setFeedback("Pega primero el enlace del video.", "error");
    urlInput.focus();
    return;
  }
  let parsed;
  try { parsed = new URL(value); } catch {
    setFeedback("El enlace no parece válido. Comprueba que incluya https://", "error");
    urlInput.focus();
    return;
  }
  if (parsed.protocol !== "https:") {
    setFeedback("Por seguridad, Vydio solo acepta enlaces HTTPS.", "error");
    return;
  }

  clearTimeout(pollingTimer);
  analyzedVideo = null;
  analyzedGallery = null;
  galleryResults?.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  thumbnailWrap?.classList.add("hidden");
  progressPanel.classList.add("hidden");
  fileLink.classList.add("hidden");
  progressPanel.classList.add("hidden");
  fileLink.classList.add("hidden");
  setBusy(analyzeButton, true, "Analizando…");
  setGlobalLoading(true);
  setFeedback("Analizando el enlace y buscando calidades disponibles…", "loading");

  try {
    const response = await fetch(apiUrl("/api/analyze"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: parsed.href })
    });
    const data = await readApiResponse(response);
    if (data.media_type === "image_gallery") {
      analyzedVideo = null;
      analyzedGallery = data;
      renderGallery(data);
      setFeedback(
        "Carrusel detectado: " + data.image_count + " imágenes listas para seleccionar.",
        data.truncated ? "warning" : "success"
      );
      return;
    }
    analyzedGallery = null;
    galleryResults?.classList.add("hidden");
    analyzedVideo = data;
    titleEl.textContent = data.title || "Video detectado";
    if (data.thumbnail && /^https:\/\//i.test(data.thumbnail)) {
      thumbnail.src = data.thumbnail;
      thumbnailWrap.classList.remove("hidden");
    } else {
      thumbnail.removeAttribute("src");
      thumbnailWrap.classList.add("hidden");
    }
    const sourceName = data.extractor || new URL(value).hostname;
    sourceEl.textContent = sourceName + " · " + formatDuration(data.duration);
    bestQualityEl.textContent = data.best_height + "p";
    qualitySelect.replaceChildren();
    data.available_heights.forEach(item => {
      const option = document.createElement("option");
      option.value = String(item.height);
      option.textContent = item.label + (item.ext ? " · " + item.ext.toUpperCase() : "");
      qualitySelect.appendChild(option);
    });
    qualitySelect.value = String(data.best_height);
    resultsPanel.classList.remove("hidden");
    if (qualityMode === "manual") manualQuality.classList.remove("hidden");
    updateDownloadLabel();
    setFeedback("Se encontraron " + data.available_heights.length + " resoluciones. La mayor disponible es " + data.best_height + "p.", "success");
  } catch (error) {
    const notConfigured = !API_BASE;
    setFeedback(error.message || "No se pudo analizar este enlace.", notConfigured ? "warning" : "error");
    if (notConfigured) setupApiLink?.classList.remove("hidden");
  } finally {
    setBusy(analyzeButton, false, "Analizar enlace");
    setGlobalLoading(false);
  }
}

async function startDownload() {
  if (!analyzedVideo) return setFeedback("Primero analiza un enlace de video compatible.", "error");
  activeDownloadKind = "video";
  const selectedHeight = qualityMode === "manual" ? Number(qualitySelect.value) : null;
  if (qualityMode === "manual" && !selectedHeight) return setFeedback("Selecciona una resolución disponible.", "error");

  setDownloadBusy(true);
  setGalleryBusy(false);
  setGlobalLoading(true);
  setFeedback("Preparando la descarga en el servidor…", "loading");
  fileLink.classList.add("hidden");
  progressPanel.classList.remove("hidden");
  progressTrack?.classList.add("is-indeterminate");
  progressBar.style.width = "0%";
  progressValue.textContent = "0%";
  progressMessage.textContent = "Enviando solicitud al servidor…";
  try {
    const response = await fetch(apiUrl("/api/download"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: urlInput.value.trim(),
        quality_mode: qualityMode,
        height: selectedHeight
      })
    });
    const data = await readApiResponse(response);
    activeJobId = data.job_id;
    pollJob(activeJobId);
  } catch (error) {
    progressPanel.classList.add("hidden");
    setDownloadBusy(false);
    setGlobalLoading(false);
    setFeedback(error.message || "No se pudo iniciar la descarga.", "error");
  }
}

async function startGalleryDownload() {
  if (!analyzedGallery) return setFeedback("Primero analiza un carrusel de TikTok.", "error");
  const selected = [...gallerySelected].sort((a, b) => a - b);
  if (!selected.length) return setFeedback("Selecciona al menos una imagen.", "error");

  activeDownloadKind = "gallery";
  setGalleryBusy(true);
  setGlobalLoading(true);
  setFeedback("Preparando el ZIP con las imágenes seleccionadas…", "loading");
  fileLink.classList.add("hidden");
  progressPanel.classList.remove("hidden");
  downloadProgressTrack?.classList.add("is-indeterminate");
  progressBar.style.width = "0%";
  progressValue.textContent = "0%";
  progressMessage.textContent = "Enviando las imágenes al servidor…";
  try {
    const response = await fetch(apiUrl("/api/gallery-download"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gallery_id: analyzedGallery.gallery_id,
        image_indices: selected
      })
    });
    const data = await readApiResponse(response);
    activeJobId = data.job_id;
    pollJob(activeJobId);
  } catch (error) {
    setGalleryBusy(false);
    progressPanel.classList.add("hidden");
    setGlobalLoading(false);
    setFeedback(error.message || "No se pudo iniciar el ZIP.", "error");
  }
}

async function pollJob(jobId) {
  try {
    const response = await fetch(apiUrl("/api/jobs/" + encodeURIComponent(jobId)));
    const job = await readApiResponse(response);
    const percent = Number.isFinite(job.progress) ? Math.max(0, Math.min(100, job.progress)) : 0;
    progressBar.style.width = percent + "%";
    progressValue.textContent = percent + "%";
    progressMessage.textContent = job.message || "Procesando…";
    if (percent > 0) {
      downloadProgressTrack?.classList.remove("is-indeterminate");
      setGlobalLoading(true, percent);
    } else {
      downloadProgressTrack?.classList.add("is-indeterminate");
      setGlobalLoading(true);
    }

    if (job.status === "finished" && job.download_ready) {
      fileLink.href = apiUrl("/api/jobs/" + encodeURIComponent(jobId) + "/file");
      fileLink.classList.remove("hidden");
      const readyName = job.filename || (activeDownloadKind === "gallery" ? "ZIP del carrusel" : "video");
      progressMessage.textContent = "Archivo listo: " + readyName;
      fileLink.lastChild.textContent = activeDownloadKind === "gallery"
        ? " Descargar ZIP (" + readyName + ")"
        : " Descargar archivo preparado (" + readyName + ")";
      progressBar.style.width = "100%";
      progressValue.textContent = "100%";
      downloadProgressTrack?.classList.remove("is-indeterminate");
      setDownloadBusy(false);
      setGalleryBusy(false);
      setGlobalLoading(false);
      setFeedback(activeDownloadKind === "gallery"
        ? "El ZIP del carrusel está listo. Pulsa el enlace para guardarlo."
        : "La descarga está preparada. Pulsa el enlace para guardarla.", "success");
      return;
    }
    if (job.status === "error") {
      downloadProgressTrack?.classList.remove("is-indeterminate");
      setDownloadBusy(false);
      setGalleryBusy(false);
      setGlobalLoading(false);
      setFeedback(job.message || "No se pudo preparar el archivo.", "error");
      return;
    }
    pollingTimer = setTimeout(() => pollJob(jobId), 1400);
  } catch (error) {
    downloadProgressTrack?.classList.remove("is-indeterminate");
    setDownloadBusy(false);
    setGalleryBusy(false);
    setGlobalLoading(false);
    setFeedback(error.message || "Se perdió la conexión con el servidor.", "error");
  }
}

analyzeButton.addEventListener("click", analyzeVideo);
downloadButton.addEventListener("click", startDownload);
galleryDownloadButton?.addEventListener("click", startGalleryDownload);
urlInput.addEventListener("keydown", event => {
  if (event.key === "Enter") analyzeVideo();
});

async function checkApi() {
  if (!API_BASE) return;
  try {
    const response = await fetch(apiUrl("/health"));
    if (!response.ok) throw new Error("API unavailable");
    apiIndicator.innerHTML = "<i></i> API conectada";
    apiIndicator.classList.add("connected");
  } catch {
    apiIndicator.innerHTML = "<i></i> API sin conexión";
  }
}
if (!API_BASE) setupApiLink?.classList.remove("hidden");
checkApi();


// Progressive Web App installation and offline app-shell support.
let deferredInstallPrompt = null;

window.addEventListener("beforeinstallprompt", event => {
  event.preventDefault();
  deferredInstallPrompt = event;
  installAppButton?.classList.remove("hidden");
});

installAppButton?.addEventListener("click", async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  const choice = await deferredInstallPrompt.userChoice;
  if (choice?.outcome === "accepted") {
    installAppButton.classList.add("hidden");
  }
  deferredInstallPrompt = null;
});

window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  installAppButton?.classList.add("hidden");
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js", { scope: "./" })
      .then(registration => registration.update())
      .catch(error => console.warn("Vydio offline support could not be enabled:", error));
  });
}


/* Progressive reveal motion: animate sections as they enter the viewport. */
(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !("IntersectionObserver" in window)) return;

  const targets = document.querySelectorAll(
    ".hero-copy, .download-panel, .platform-section, .section-heading, .step-card, .responsible-notice"
  );
  if (!targets.length) return;

  document.documentElement.classList.add("motion-enabled");
  const observer = new IntersectionObserver((entries, instance) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      instance.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -36px 0px" });

  targets.forEach((element, index) => {
    element.classList.add("reveal-on-scroll");
    if (element.classList.contains("step-card")) {
      element.style.setProperty("--reveal-delay", (index % 3) * 90 + "ms");
    }
    observer.observe(element);
  });
})();
