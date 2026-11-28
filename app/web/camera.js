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
let currentObservation;
let analysisFresh = false;
let registering = false;
let registered = false;
let passageWorkflow = false;

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
  const busy = classifying || reading || registering;
  element("register-parking").disabled = busy || passageWorkflow || !analysisFresh || !currentObservation || registered;
  cameraSelect.disabled = busy;
  trigger.disabled = busy;
  element("test-image").disabled = busy;
  resume.disabled = busy;
  readPlate.disabled = !snapshot || (!box && capturedCamera !== "exit") || busy;
  readPlate.textContent = capturedCamera === "exit" && !box ? "Find and read plate" : "Read selected plate";
  comparePlate.disabled = !snapshot || !box || reading || classifying;
  element("expected-plate").disabled = reading || classifying;
  element("enhance-comparison").disabled = reading || classifying;
  element("include-paddle").disabled = reading || classifying;
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
  currentObservation = undefined;
  analysisFresh = false;
  registered = false;
  status("parking-decision", "", "Analyze the photo, review the plate, then check the gate.");
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
    status("capture-status", "pending", capturedCamera === "entrance" ? "Classifying the vehicle and reading the plate…" : "Finding and reading the exit plate…");
    await analyzeParkingPhoto();
  } catch (error) {
    status("capture-status", "error", stage.hidden ? "Camera capture failed." : "Parking analysis failed.");
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
  if (!event.isPrimary || event.button !== 0 || reading || classifying || registering) return;
  analysisFresh = false;
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

async function analyzeParkingPhoto() {
  const data = snapshotForm();
  if (box) data.append("plate_bbox", box.join(","));
  if (currentObservation) data.append("previous_observation_id", currentObservation.observation_id);
  const result = await requestJSON("/parking/analyze", data);
  currentObservation = result;
  analysisFresh = true;
  registered = false;
  if (result.plate_bbox) { box = result.plate_bbox; showSelectedBox(); }
  const names = { car: "Passenger car", sportcar: "Sports car", supercar: "Supercar", unknown: "Unknown" };
  element("vehicle").textContent = capturedCamera === "exit" ? "Matched from entry on exit" : (names[result.vehicle_type] || "Unknown");
  element("model").textContent = result.model || result.plate_box?.model || "—";
  element("gemini-time").textContent = seconds(result.processing_ms.vehicle ?? result.plate_box?.processing_ms);
  element("plate").textContent = result.license_plate || "Unreadable";
  element("enhancement-time").textContent = seconds(result.preprocess_ms);
  element("ocr-time").textContent = seconds(result.ocr_ms);
  if (result.enhanced_plate) {
    element("enhanced-plate").src = result.enhanced_plate;
    element("enhanced-plate").hidden = false;
  }
  status("capture-status", result.eligible ? "success" : "review", result.eligible ? "Analysis saved. Review the plate, then check the gate." : "Analysis saved. Recognition needs review.");
  const boxSource = { gemini: "Gemini plate box", heuristic: "Local plate box", heuristic_fallback: "Local fallback box", manual: "Selected box" }[result.plate_box?.source];
  const plateMessage = result.license_plate
    ? `${boxSource ? `${boxSource}. ` : ""}Check the reading and redraw the box if needed.`
    : "Plate unreadable. Draw a box around the registration characters and try again.";
  status("plate-status", result.eligible ? "success" : "review", plateMessage);
  errorMessage("capture-error", result.vehicle?.error);
  errorMessage("plate-error", result.errors.filter((error) => error !== result.vehicle?.error).join(" · "));
  status("parking-decision", "", passageWorkflow
    ? "Photo saved for review. The gate workflow starts with a sensor arrival."
    : "Photo saved. Check the gate to allocate or release a matching space.");
  updateControls();
}

async function readSelectedPlate() {
  if (!snapshot || (!box && capturedCamera !== "exit") || reading || registering) return;
  reading = true;
  analysisFresh = false;
  updateControls();
  status("plate-status", "pending", "Enhancing, reading, and saving the photo…");
  clearPlateResult();
  try {
    await analyzeParkingPhoto();
  } catch (error) {
    status("plate-status", "error", "Could not analyze and save this photo.");
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
    const exitPhoto = capturedCamera === "exit";
    status("capture-status", "", exitPhoto ? "Test photo loaded. Read the plate or draw a box." : "Test photo loaded. Draw a box around the plate.");
    status("plate-status", "", exitPhoto ? "Click Find and read plate, or draw your own box." : "Draw a box, then read it or compare OCR models.");
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
  if (!snapshot || !box || reading || classifying || registering) return;
  analysisFresh = false;
  reading = true;
  updateControls();
  status("plate-status", "pending", "Comparing the selected OCR models on the same crop…");
  clearPlateResult();
  try {
    const data = snapshotForm();
    data.append("plate_bbox", box.join(","));
    const expected = element("expected-plate").value.trim();
    if (expected) data.append("expected", expected);
    data.append("enhance", String(element("enhance-comparison").checked));
    data.append("include_paddle", String(element("include-paddle").checked));
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
    status("plate-status", failed ? "review" : "success", failed ? "Comparison finished with an engine error." : "Comparison complete. Review the readings below.");
  } catch (error) {
    status("plate-status", "error", "OCR comparison failed.");
    errorMessage("plate-error", error.message);
  } finally {
    reading = false;
    updateControls();
  }
});

const decisionMessages = {
  entry_registered: "Entry registered",
  exit_registered: "Exit registered; space released",
  normal_full: "No normal bay is available. Normal cars cannot use VIP bays",
  vip_full: "No VIP bay is available. Sports cars and supercars cannot use normal bays",
  already_inside: "This plate is already registered inside",
  no_active_session: "No active visit matches this exit plate",
  recognition_needs_review: "Plate or vehicle recognition needs review",
  unsupported_vehicle_type: "Vehicle type is not supported",
};
let parkingRefreshing = false;
let parkingRefreshVersion = 0;
let gateCloseTimer;

function renderParkingStatus(state) {
  passageWorkflow = state.workflow === "passage";
  element("register-parking").hidden = passageWorkflow;
  if (passageWorkflow) {
    element("parking-gate").textContent = state.gate_mode === "physical_inferred_test"
      ? "Physical demo · passage inferred from clean gate cycles"
      : "Supervised passage simulation";
  }
  updateControls();
  element("vip-available").textContent = state.capacity.vip.available;
  element("normal-available").textContent = state.capacity.normal.available;
  status("parking-cloud", "live", state.storage === "supabase" ? "Supabase connected" : "Local database ready");
  const list = element("parking-spaces");
  list.replaceChildren();
  for (const bay of state.spaces) {
    const card = document.createElement("div");
    card.className = "space-card";
    card.dataset.state = bay.status;
    const name = document.createElement("strong");
    name.textContent = bay.id;
    const occupancy = document.createElement("span");
    occupancy.textContent = { free: "Available", occupied: "Occupied", reserved: "Reserved", review: "Review", sensor_occupied: "Sensor occupied", sensor_stale: "Sensor stale", sensor_unknown: "Sensor unknown" }[bay.status] || "Unavailable";
    const plate = document.createElement("span");
    plate.textContent = bay.plate || (bay.kind === "vip" ? "Sports / supercar" : "Normal car");
    card.append(name, occupancy, plate);
    list.append(card);
  }
}

function parkingTime(value) {
  return value ? new Date(value).toLocaleString("en-GB", {timeZone:"Asia/Bangkok"}) : "—";
}

function tableCell(row, text) {
  const cell = document.createElement("td");
  cell.textContent = text ?? "—";
  row.append(cell);
  return cell;
}

function renderParkingHistory(history) {
  const visits = element("parking-visits");
  visits.replaceChildren();
  for (const visit of history.sessions) {
    const row = document.createElement("tr");
    tableCell(row, visit.plate);
    tableCell(row, `${visit.vehicle_type} · ${visit.space_id || "—"}`);
    tableCell(row, visit.status === "still_in" ? "Still inside" : "Left");
    tableCell(row, parkingTime(visit.entered_at) + (visit.entry_passage_basis === "inferred_clearance" ? " · Inferred" : ""));
    tableCell(row, parkingTime(visit.exited_at) + (visit.exit_passage_basis === "inferred_clearance" ? " · Inferred" : ""));
    const photos = tableCell(row, "");
    for (const [label, url] of [["Entry", visit.entrance_photo_url], ["Exit", visit.exit_photo_url]]) {
      if (!url?.startsWith("/parking/media/")) continue;
      const link = document.createElement("a");
      link.href = url; link.target = "_blank"; link.rel = "noopener"; link.textContent = `${label} photo `;
      photos.append(link);
    }
    visits.append(row);
  }
  element("parking-history-note").textContent = history.sessions.length ? "Showing recent visits. Times are in Bangkok time." : "No parking visits yet.";
  const events = element("parking-events");
  events.replaceChildren();
  for (const event of history.events) {
    const row = document.createElement("tr");
    tableCell(row, parkingTime(event.created_at));
    tableCell(row, event.camera === "entrance" ? "Entry" : "Exit");
    tableCell(row, event.plate);
    tableCell(row, `${event.approved ? "Allowed" : "Denied"}: ${decisionMessages[event.reason] || event.reason}${event.passage_basis === "inferred_clearance" ? " · Inferred" : ""}`);
    tableCell(row, event.space_id);
    events.append(row);
  }
}

async function parkingGET(path) {
  const response = await fetch(path, {cache:"no-store"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Parking service unavailable");
  return result;
}

async function refreshParking() {
  if (parkingRefreshing) return;
  parkingRefreshing = true;
  const version = ++parkingRefreshVersion;
  try {
    const state = await parkingGET("/parking/status");
    if (version === parkingRefreshVersion) renderParkingStatus(state);
    try {
      const history = await parkingGET("/parking/history?limit=20");
      if (version === parkingRefreshVersion) renderParkingHistory(history);
    } catch (error) {
      element("parking-history-note").textContent = error.message;
    }
  } catch (error) {
    if (version === parkingRefreshVersion) {
      status("parking-cloud", "offline", "Database unavailable");
      element("vip-available").textContent = "—";
      element("normal-available").textContent = "—";
      element("parking-spaces").textContent = "Availability unknown. Gate checks fail closed while the database is unreachable.";
    }
  } finally { parkingRefreshing = false; }
}

element("register-parking").addEventListener("click", async () => {
  if (passageWorkflow || !analysisFresh || !currentObservation || registering || registered) return;
  registering = true;
  updateControls();
  status("parking-decision", "pending", "Checking the matching bays and recording the decision…");
  try {
    const response = await fetch("/parking/commit", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({observation_id:currentObservation.observation_id,request_id:currentObservation.observation_id}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Parking decision failed");
    registered = true;
    const decision = result.decision;
    const explanation = decisionMessages[decision.reason] || decision.reason;
    status("parking-decision", decision.approved ? "success" : "review", `${explanation}${decision.space_id ? ` · ${decision.space_id}` : ""}.`);
    const newOpen = decision.approved && !decision.replayed;
    element("parking-gate").dataset.state = newOpen ? "open" : "closed";
    window.clearTimeout(gateCloseTimer);
    element("parking-gate").textContent = newOpen ? `Gate simulator · OPEN for ${decision.camera === "entrance" ? "entry" : "exit"}` : decision.replayed ? "Gate simulator · CLOSED · previous decision recovered; no new OPEN" : "Gate simulator · CLOSED";
    if (newOpen) {
      gateCloseTimer = window.setTimeout(() => {
        element("parking-gate").dataset.state = "closed";
        element("parking-gate").textContent = "Gate simulator · CLOSED after the 4-second simulation";
      }, 4000);
    }
    ++parkingRefreshVersion;
    if (result.parking) renderParkingStatus(result.parking);
    await refreshParking();
  } catch (error) {
    status("parking-decision", "error", error.message);
    element("parking-gate").dataset.state = "closed";
    element("parking-gate").textContent = "Gate simulator · no new OPEN confirmed. Retry the same check to recover a recorded decision.";
  } finally {
    registering = false;
    updateControls();
  }
});

element("refresh-parking").addEventListener("click", refreshParking);
applyMirror();
startPreview();
refreshParking();
window.setInterval(() => { if (!document.hidden) refreshParking(); }, 10000);
