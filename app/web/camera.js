const element = (id) => document.getElementById(id);
const previewMessage = element("preview-message");
const stage = element("snapshot-stage");
const still = element("snapshot");
const boxOutline = element("plate-box");
const trigger = element("trigger");
const resume = element("resume");
const readPlate = element("read-plate");
const comparePlate = element("compare-plate");
const flip = element("flip");
const cameraSelect = element("camera-select");
const cameraNames = { entrance: "Entrance · Coming in", exit: "Exit · Going out" };
const streams = Object.keys(cameraNames).map((role) => ({
  role,
  image: element(`${role}-preview`),
  message: element(`${role}-message`),
  reconnect: undefined,
}));
let capturedCamera = "entrance";

let live = true;
let snapshot;
let snapshotURL;
let box;
let drag;
let classifying = false;
let reading = false;
let showSensorOrientation = false;

function applyMirror() {
  // Camera frames arrive corrected. One display flip shows the sensor orientation.
  element("viewfinder").dataset.mirrored = String(showSensorOrientation);
  document.querySelectorAll(".live-view").forEach((view) => { view.dataset.mirrored = String(showSensorOrientation); });
  flip.setAttribute("aria-pressed", String(!showSensorOrientation));
  showSelectedBox();
}

flip.addEventListener("click", () => {
  showSensorOrientation = !showSensorOrientation;
  applyMirror();
});

function status(id, state, message) {
  const label = element(id);
  label.dataset.state = state;
  label.textContent = message;
}

function errorMessage(id, message) {
  const label = element(id);
  label.textContent = message || "";
  label.hidden = !message;
}

function clearPlateResult() {
  errorMessage("plate-error", "");
  element("enhanced-plate").hidden = true;
  element("ocr-comparison").hidden = true;
  element("ocr-comparison-rows").replaceChildren();
  ["plate", "enhancement-time", "ocr-time"].forEach((id) => { element(id).textContent = "—"; });
}

function seconds(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) / 1000).toFixed(2)} s`;
}

function updateControls() {
  cameraSelect.disabled = classifying || reading;
  trigger.disabled = classifying || reading;
  element("test-image").disabled = classifying || reading;
  resume.disabled = classifying || reading;
  readPlate.disabled = !snapshot || !box || reading;
  comparePlate.disabled = !snapshot || !box || reading || classifying;
  element("expected-plate").disabled = reading || classifying;
  element("enhance-comparison").disabled = reading || classifying;
}

function startPreview() {
  for (const stream of streams) {
    if ((!live && stream.role === capturedCamera) || stream.image.hasAttribute("src")) continue;
    window.clearTimeout(stream.reconnect);
    status(`${stream.role}-status`, "connecting", "Connecting");
    stream.message.textContent = `Waiting for ${cameraNames[stream.role]}…`;
    stream.message.hidden = false;
    stream.image.src = `/camera/stream?camera=${stream.role}&attempt=${Date.now()}`;
  }
}

cameraSelect.addEventListener("change", () => {
  clearPlateResult();
  ["vehicle", "model", "camera-time", "gemini-time", "capture-camera"].forEach((id) => { element(id).textContent = "—"; });
  status("capture-status", "", "Ready for a test.");
  resumePreview();
});

for (const stream of streams) {
  stream.image.addEventListener("load", () => {
    if (!live && stream.role === capturedCamera) return;
    status(`${stream.role}-status`, "live", "Live");
    stream.message.hidden = true;
    stream.image.hidden = false;
  });
  stream.image.addEventListener("error", () => {
    if (!live && stream.role === capturedCamera) return;
    status(`${stream.role}-status`, "offline", "Offline");
    stream.message.textContent = "Camera unavailable. Reconnecting…";
    stream.message.hidden = false;
    stream.image.hidden = true;
    stream.image.removeAttribute("src");
    window.clearTimeout(stream.reconnect);
    stream.reconnect = window.setTimeout(startPreview, 2000);
  });
}

function clearSelection() {
  snapshot = undefined;
  box = undefined;
  drag = undefined;
  stage.hidden = true;
  boxOutline.hidden = true;
  still.removeAttribute("src");
  if (snapshotURL) URL.revokeObjectURL(snapshotURL);
  snapshotURL = undefined;
  element("expected-plate").value = "";
}

function resumePreview() {
  live = true;
  clearSelection();
  resume.hidden = true;
  element("preview-note").textContent = "Live views above stay on your local network.";
  previewMessage.textContent = "Select a camera and simulate an alert to take a photo.";
  previewMessage.hidden = false;
  status("preview-status", "", "No capture");
  status("plate-status", "", "Capture a photo to find the license plate.");
  startPreview();
  updateControls();
}

resume.addEventListener("click", resumePreview);

async function requestJSON(url, data) {
  const response = await fetch(url, { method: "POST", body: data });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(typeof result.detail === "string" ? result.detail : "Request could not be completed.");
  }
  return result;
}

function snapshotForm() {
  const form = new FormData();
  form.append("image", snapshot, "esp32_capture.jpg");
  form.append("camera", capturedCamera);
  return form;
}

trigger.addEventListener("click", async () => {
  capturedCamera = cameraSelect.value;
  classifying = true;
  live = false;
  const stream = streams.find((item) => item.role === capturedCamera);
  window.clearTimeout(stream.reconnect);
  stream.image.removeAttribute("src");
  stream.image.hidden = true;
  stream.message.textContent = "Paused for capture";
  stream.message.hidden = false;
  status(`${stream.role}-status`, "", "Paused");
  clearSelection();
  resume.hidden = true;
  ["vehicle", "model", "camera-time", "gemini-time"].forEach((id) => {
    element(id).textContent = "—";
  });
  element("capture-camera").textContent = cameraNames[capturedCamera];
  errorMessage("capture-error", "");
  clearPlateResult();
  status("preview-status", "connecting", "Capturing");
  previewMessage.textContent = "Taking a fresh photo…";
  previewMessage.hidden = false;
  status("capture-status", "pending", "Waiting for the camera…");
  status("plate-status", "", "Waiting for the photo…");
  updateControls();

  try {
    const photo = await fetch(`/camera/snapshot?camera=${capturedCamera}`, { cache: "no-store" });
    if (!photo.ok) {
      const { detail } = await photo.json();
      throw new Error(detail || "Could not take a photo.");
    }

    snapshot = await photo.blob();
    snapshotURL = URL.createObjectURL(snapshot);
    still.src = snapshotURL;
    await still.decode();
    previewMessage.hidden = true;
    stage.hidden = false;
    resume.hidden = false;
    status("preview-status", "", "Captured still");
    element("preview-note").textContent = "Drag a tight box to adjust the plate selection.";
    element("camera-time").textContent = seconds(photo.headers.get("X-Camera-Ms"));
    if (capturedCamera === "exit") {
      status("capture-status", "success", "Exit photo captured for local plate OCR.");
      await readSelectedPlate();
      return;
    }
    status("capture-status", "pending", "Gemini is classifying the vehicle…");
    status("plate-status", "pending", "Finding the plate. You can also draw its box.");

    const result = await requestJSON("/classify", snapshotForm());
    const names = { car: "Passenger car", sportcar: "Sports car", supercar: "Supercar", unknown: "Unknown" };
    element("vehicle").textContent = names[result.vehicle_type] ?? "Unknown";
    element("model").textContent = result.model || "—";
    element("gemini-time").textContent = seconds(result.processing_ms);
    if (result.error && result.confidence == null) {
      status("capture-status", "error", "Gemini classification failed.");
    } else {
      status("capture-status", result.needs_review ? "review" : "success", result.needs_review ? "Classification needs review." : "Vehicle classified.");
    }
    errorMessage("capture-error", result.error);
    if (!box && !drag) {
      if (result.plate_bbox) {
        box = result.plate_bbox;
        showSelectedBox();
        await readSelectedPlate();
      } else {
        status("plate-status", "review", "No clear plate found. Draw a box around it to read it.");
      }
    }
  } catch (error) {
    status("capture-status", "error", stage.hidden ? "Camera capture failed." : "Gemini request failed.");
    errorMessage("capture-error", error.message);
    if (stage.hidden) resumePreview();
    else if (!box && !drag) status("plate-status", "review", "Draw a box around the plate to read it.");
  } finally {
    classifying = false;
    updateControls();
  }
});

function imagePoint(event) {
  const rect = still.getBoundingClientRect();
  const clamp = (value) => Math.max(0, Math.min(1, value));
  return [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)];
}

function paintBox(x, y, right, bottom) {
  Object.assign(boxOutline.style, {
    left: `${x * 100}%`, top: `${y * 100}%`,
    width: `${(right - x) * 100}%`, height: `${(bottom - y) * 100}%`,
  });
  boxOutline.hidden = false;
}

function showSelectedBox() {
  if (!box) return;
  const [x1, y1, x2, y2] = box;
  const { naturalWidth: width, naturalHeight: height } = still;
  const left = showSensorOrientation ? width - x2 : x1;
  const right = showSensorOrientation ? width - x1 : x2;
  paintBox(left / width, y1 / height, right / width, y2 / height);
}

function drawBox(event) {
  const end = imagePoint(event);
  const x = Math.min(drag.start[0], end[0]);
  const y = Math.min(drag.start[1], end[1]);
  const right = Math.max(drag.start[0], end[0]);
  const bottom = Math.max(drag.start[1], end[1]);
  paintBox(x, y, right, bottom);
  // Plate coordinates always refer to the corrected photo used by the server.
  const imageLeft = showSensorOrientation ? 1 - right : x;
  const imageRight = showSensorOrientation ? 1 - x : right;
  return [
    Math.floor(imageLeft * still.naturalWidth), Math.floor(y * still.naturalHeight),
    Math.ceil(imageRight * still.naturalWidth), Math.ceil(bottom * still.naturalHeight),
  ];
}

stage.addEventListener("pointerdown", (event) => {
  if (!event.isPrimary || event.button !== 0 || reading) return;
  drag = { id: event.pointerId, start: imagePoint(event) };
  stage.setPointerCapture(drag.id);
  box = undefined;
  clearPlateResult();
  drawBox(event);
  updateControls();
});

stage.addEventListener("pointermove", (event) => {
  if (drag?.id === event.pointerId) drawBox(event);
});

stage.addEventListener("pointerup", (event) => {
  if (drag?.id !== event.pointerId) return;
  const region = drawBox(event);
  drag = undefined;
  if (region[2] - region[0] < 5 || region[3] - region[1] < 5) {
    boxOutline.hidden = true;
    status("plate-status", "review", "Draw a larger box around the license plate.");
  } else {
    box = region;
    status("plate-status", "", "Plate selected. Click Read selected plate.");
  }
  updateControls();
});

stage.addEventListener("pointercancel", (event) => {
  if (drag?.id !== event.pointerId) return;
  drag = undefined;
  boxOutline.hidden = true;
  updateControls();
});

async function readSelectedPlate() {
  if (!snapshot || (!box && capturedCamera !== "exit") || reading) return;
  reading = true;
  updateControls();
  status("plate-status", "pending", "Enhancing the crop and reading the plate…");
  clearPlateResult();

  try {
    const data = snapshotForm();
    if (box) data.append("plate_bbox", box.join(","));
    const result = await requestJSON("/plate/read", data);
    if (!box && result.bbox) {
      box = result.bbox;
      showSelectedBox();
    }
    if (result.enhanced_plate) {
      const enhanced = element("enhanced-plate");
      enhanced.src = result.enhanced_plate;
      enhanced.hidden = false;
    }
    element("plate").textContent = result.license_plate || "Unreadable";
    element("enhancement-time").textContent = seconds(result.preprocess_ms);
    element("ocr-time").textContent = seconds(result.ocr_ms);
    if (result.status === "success") {
      status("plate-status", "success", "Plate read. Redraw the box if needed.");
    } else if (result.status === "failed") {
      status("plate-status", "error", "EasyOCR could not read the plate.");
    } else if (!box) {
      status("plate-status", "review", "No clear plate found. Draw a box around the registration characters.");
    } else {
      status("plate-status", "review", "Plate needs review. Try a tighter box.");
    }
    errorMessage("plate-error", box ? result.error : "");
  } catch (error) {
    status("plate-status", "error", "Could not read the selected plate.");
    errorMessage("plate-error", error.message);
  } finally {
    reading = false;
    updateControls();
  }
}

readPlate.addEventListener("click", readSelectedPlate);

element("test-image").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file || reading || classifying) return;
  if (file.size > 15 * 1024 * 1024) {
    errorMessage("plate-error", "Choose an image smaller than 15 MiB.");
    event.target.value = "";
    return;
  }
  capturedCamera = cameraSelect.value;
  live = false;
  const stream = streams.find((item) => item.role === capturedCamera);
  window.clearTimeout(stream.reconnect);
  stream.image.removeAttribute("src");
  stream.image.hidden = true;
  stream.message.textContent = "Paused for test photo";
  stream.message.hidden = false;
  status(`${stream.role}-status`, "", "Paused");
  clearSelection();
  clearPlateResult();
  errorMessage("capture-error", "");
  ["vehicle", "model", "camera-time", "gemini-time"].forEach((id) => { element(id).textContent = "—"; });
  classifying = true;
  updateControls();
  try {
    snapshot = file;
    snapshotURL = URL.createObjectURL(file);
    still.src = snapshotURL;
    await still.decode();
    stage.hidden = false;
    previewMessage.hidden = true;
    resume.hidden = false;
    element("capture-camera").textContent = "Uploaded test photo";
    status("preview-status", "", "Test photo");
    status("capture-status", "", "Test photo loaded. Draw a box around the plate.");
    status("plate-status", "", "Draw a box, then read it or compare both engines.");
    element("preview-note").textContent = file.name;
  } catch (error) {
    resumePreview();
    errorMessage("plate-error", "Could not open this image. Try JPEG or PNG.");
  } finally {
    classifying = false;
    event.target.value = "";
    updateControls();
  }
});

comparePlate.addEventListener("click", async () => {
  if (!snapshot || !box || reading || classifying) return;
  reading = true;
  updateControls();
  status("plate-status", "pending", "Comparing EasyOCR and OpenThai on the same crop…");
  clearPlateResult();
  try {
    const data = snapshotForm();
    data.append("plate_bbox", box.join(","));
    const expected = element("expected-plate").value.trim();
    if (expected) data.append("expected", expected);
    data.append("enhance", String(element("enhance-comparison").checked));
    const result = await requestJSON("/plate/compare", data);
    element("enhanced-plate").src = result.enhanced_plate;
    element("enhanced-plate").hidden = false;
    element("enhancement-time").textContent = seconds(result.preprocess_ms);
    const body = element("ocr-comparison-rows");
    let failed = false;
    for (const item of result.results) {
      const row = document.createElement("tr");
      let outcome = item.status.replaceAll("_", " ");
      if (item.score) outcome = `${item.score.exact_match ? "Exact match" : "Mismatch"}; CER ${(item.score.cer * 100).toFixed(1)}%`;
      if (item.error) outcome += ` · ${item.error}`;
      failed ||= item.status === "failed";
      for (const text of [item.engine, item.text || "Unreadable", seconds(item.processing_ms), seconds(item.model_ready_ms), outcome]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      body.append(row);
    }
    element("ocr-comparison").hidden = false;
    status("plate-status", failed ? "review" : "success", failed ? "Comparison finished with an engine error." : "Comparison complete. Review both readings below.");
  } catch (error) {
    status("plate-status", "error", "OCR comparison failed.");
    errorMessage("plate-error", error.message);
  } finally {
    reading = false;
    updateControls();
  }
});

applyMirror();
startPreview();
