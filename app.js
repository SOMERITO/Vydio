const urlInput = document.querySelector("#video-url");
const analyzeButton = document.querySelector("#analyze-btn");
const pasteButton = document.querySelector("#paste-btn");
const feedback = document.querySelector("#feedback");
const manualQuality = document.querySelector("#manual-quality");
const qualitySelect = document.querySelector("#quality-select");
const modeButtons = [...document.querySelectorAll(".quality-option")];
let qualityMode = "auto";

function setFeedback(message) { feedback.textContent = message; }
function setMode(mode) {
  qualityMode = mode;
  modeButtons.forEach(button => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
  manualQuality.classList.toggle("hidden", mode !== "manual");
  setFeedback(mode === "auto"
    ? "Modo automático: se solicitará la máxima calidad disponible."
    : "Modo manual: podrás elegir una resolución que exista en la fuente.");
}
modeButtons.forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));

pasteButton.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text) return setFeedback("El portapapeles está vacío.");
    urlInput.value = text.trim();
    setFeedback("Enlace pegado. Puedes continuar cuando el servicio de análisis esté conectado.");
  } catch {
    setFeedback("No se pudo acceder al portapapeles. Pega el enlace directamente en el campo.");
  }
});

analyzeButton.addEventListener("click", () => {
  const value = urlInput.value.trim();
  if (!value) {
    setFeedback("Pega primero el enlace del video.");
    urlInput.focus();
    return;
  }
  let parsed;
  try { parsed = new URL(value); } catch {
    setFeedback("El enlace no parece válido. Comprueba que incluya https://");
    urlInput.focus();
    return;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    setFeedback("Solo se aceptan enlaces web HTTP o HTTPS.");
    return;
  }
  const requestedQuality = qualityMode === "auto" ? "best" : qualitySelect.value;
  setFeedback("La interfaz está lista. Falta configurar el backend de análisis y descarga; no se ha iniciado ninguna descarga.");
  console.info("Vydio request prepared", { url: parsed.href, qualityMode, requestedQuality });
});

urlInput.addEventListener("keydown", event => {
  if (event.key === "Enter") analyzeButton.click();
});
