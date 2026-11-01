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
  resultsPanel.classList.add("hidden");
  thumbnailWrap?.classList.add("hidden");
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
  if (!analyzedVideo) return setFeedback("Primero analiza un enlace compatible.", "error");
  const selectedHeight = qualityMode === "manual" ? Number(qualitySelect.value) : null;
  if (qualityMode === "manual" && !selectedHeight) return setFeedback("Selecciona una resolución disponible.", "error");

  setDownloadBusy(true);
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
      progressMessage.textContent = "Archivo listo: " + (job.filename || "video");
      progressBar.style.width = "100%";
      progressValue.textContent = "100%";
      downloadProgressTrack?.classList.remove("is-indeterminate");
      setDownloadBusy(false);
      setGlobalLoading(false);
      setFeedback("La descarga está preparada. Pulsa «Descargar archivo» para guardarla.", "success");
      return;
    }
    if (job.status === "error") {
      downloadProgressTrack?.classList.remove("is-indeterminate");
      setDownloadBusy(false);
      setGlobalLoading(false);
      setFeedback(job.message || "No se pudo preparar el archivo.", "error");
      return;
    }
    pollingTimer = setTimeout(() => pollJob(jobId), 1400);
  } catch (error) {
    downloadProgressTrack?.classList.remove("is-indeterminate");
    setDownloadBusy(false);
    setGlobalLoading(false);
    setFeedback(error.message || "Se perdió la conexión con el servidor.", "error");
  }
}

analyzeButton.addEventListener("click", analyzeVideo);
downloadButton.addEventListener("click", startDownload);
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
