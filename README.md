# Intelligence Parking Lot

Intelligence Parking Lot is a team project that combines parking sensors, license plate recognition, and barrier control. The goal is for the local laptop to capture an image, validate the plate and vehicle type, and decide whether to open the barrier. The laptop then syncs the camera record and parking session to the cloud in the background.

## Repository scope

This repository covers the **ESP32-CAM, the local camera and recognition service, and the planned cloud storage for camera and parking-session records**. The local service accepts a JPEG or takes a still from the ESP32-CAM, identifies the vehicle, reads the plate, and returns structured results for parking decisions. The repository also includes a CLI, example responses, tests, and an evaluation script.

The team dashboard is ready for sensor data. Our teammate owns the **sensor cloud storage** implementation. The camera record can feed the dashboard so it can display the photo, vehicle type, plate, parking status, and entry and exit times.

## Project status

| Component | Current status |
| --- | --- |
| ESP32-CAM and test page | Camera firmware and setup, live preview, a simulated sensor alert that captures a fresh still, and manual correction of the plate crop. |
| Recognition | Gemini classifies the vehicle and suggests the plate crop. OpenCV prepares the crop, and EasyOCR reads Thai and English plates locally. The service returns the result, review status, and processing times. |
| Team dashboard | A teammate completed the sensor dashboard and owns the upcoming sensor cloud storage work. |
| Remaining integration | Connect the real arrival trigger, parking decisions, servo control, and camera cloud storage, then test the complete entry and exit flow. |

The local prototype distinguishes passenger cars (`car`), sports cars (`sportcar`), and supercars (`supercar`). It returns `unknown` for a capture that needs review. All three car classes belong to the car bay category. Motorcycle classification is still to do.

## How the full system works

| Component | Role in the integrated system |
| --- | --- |
| Arduino UNO R3 | Reads the A1 and M1 ultrasonic sensors and drives the barrier servo. A dedicated entrance detector or an operator action supplies the arrival trigger. |
| ESP32 DevKit gateway | Carries occupancy and arrival events from the UNO to the local server and sends gate commands back to the UNO. It also handles its own sensor readings. |
| ESP32-CAM | Sends one still image directly to the local server over Wi-Fi. |
| Local laptop or on-site server | Requests the image, obtains and validates plate text and vehicle type, checks the matching bay, and sends the gate command. |
| Team dashboard | Displays sensor readings and can display camera and parking-session results once integrated. |
| Camera cloud storage | Receives the camera and session record from the local laptop for later viewing. |

1. For automatic capture at the gate, an entrance detector sends an arrival event through the UNO and ESP32 gateway. The A1 and M1 sensors track occupancy inside the bays.
2. The local laptop requests one JPEG from the ESP32-CAM. It obtains the plate text and vehicle type for that same arrival.
3. On entry, the laptop validates the result and reserves a matching available bay. On exit, it checks the active parking record for the plate. Unclear or failed recognition goes to operator review.
4. The laptop sends a small `OPEN` command through the ESP32 gateway to the UNO, which drives the servo. The laptop then queues the camera and session record for background upload.

The Gemini classification call happens before the gate decision. EasyOCR runs on the laptop, and permanent cloud storage happens after the open command.

## Camera cloud storage plan

Store one record per parking visit under a stable session ID. The record contains these fields.

| Field | What the cloud stores |
| --- | --- |
| `image` | The captured vehicle photo in object storage, referenced by the session record. |
| `vehicle_type` | The validated vehicle type. |
| `license_plate` | The validated OCR plate text. |
| `status` | `still_in` after entry and `left` after a matched exit. |
| `entered_at` | The date and time the vehicle entered the parking lot. |
| `exited_at` | The date and time the vehicle left the parking lot. This is `null` while the vehicle is still inside. |

On entry, the laptop queues a new `still_in` record with `entered_at` set and `exited_at` empty. On a matched exit, it updates that same session to `left` and records `exited_at`. Store both timestamps in UTC and display them in the parking lot's local time. Use the same session ID for upload retries and upload changes in order so a delayed entry update cannot overwrite a completed visit.

## What to do next

1. **Connect the arrival trigger.** Verify the UNO-to-gateway link and trigger one capture per arrival. Use the existing simulated alert to test the camera flow before connecting hardware.
2. **Complete recognition for parking.** Add motorcycle classification and evaluate both vehicle types and Thai plate OCR with real ESP32-CAM frames. Route unclear readings to operator review.
3. **Connect the gate decision.** Reserve a matching entry bay or match the exit plate to an active session before issuing `OPEN`. Record entry and exit times locally.
4. **Implement camera cloud storage.** Add background photo uploads and session updates for the camera record fields above. Connect the dashboard to the camera records and keep the sensor storage work with our teammate.
5. **Test entry and exit end to end.** Verify that entry and exit times appear on the same session, the status changes from `still_in` to `left`, and failed archive uploads can retry without delaying gate control. Also test a full matching bay, unreadable plates, and duplicate arrival messages.

## Run the camera and AI prototype

Python 3.11 is recommended. From the repository root, create and activate a virtual environment.

```bash
# macOS / Linux
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp -n .env.example .env
```

```powershell
# Windows PowerShell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set `GEMINI_API_KEY` in `.env`. The entrance classifier defaults to `gemini-4-hydrogen` with low thinking and requests the vehicle type and plate box together. If Gemini is busy, rate-limited, or times out, the default configuration tries `gemini-3.1-flash-lite` once. Each attempt has an eight-second timeout, and the app shows which model returned the result. Set `GEMINI_MODEL` to override the default.

For camera input, set `ESP32_CAPTURE_URL` to the camera's still-image endpoint, such as `http://192.168.1.50/capture`. Put the camera and laptop on the same reachable network. See the [camera setup guide](camera/README.md).

```bash
# Replace this path with a local test image.
python -m app.cli --image data/samples/vehicle.jpg

# Or take a still using ESP32_CAPTURE_URL.
python -m app.cli --camera-url

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [the API docs](http://127.0.0.1:8000/docs) to try `POST /infer` with an uploaded image or `POST /capture` with the configured camera. Both accept an optional `plate_bbox` in original-image pixel coordinates, `x1,y1,x2,y2`. The result includes the vehicle class, recognized plate, review status, and processing times. See the [example response](examples/sample_response.json).

### Live camera test

Flash the AI Thinker-compatible sketch in `camera/parking_camera/` using the [camera setup guide](camera/README.md). With the ESP32-CAM-MB connected by USB, run `python -m app.camera_setup` to find the camera, save a test frame, and set its URL.

Start the local server and open [the camera test page](http://127.0.0.1:8000/). Click **Simulate sensor alert** to pause the preview and take a fresh photo. The page shows the vehicle class, the enhanced plate crop, the OCR result, and timings. If the crop is wrong, draw a box around the plate and click **Read selected plate**. EasyOCR downloads its Thai and English weights on first use and reuses them locally afterward.

### Compare EasyOCR and OpenThai OCR Text 2.0

Install the optional local ONNX engine and download the pinned model once:

```bash
python -m pip install -r requirements-ocr-compare.txt
python scripts/setup_openthai.py
```

On the camera test page, capture a photo or choose **Or open a test photo**, draw a box around the registration characters, optionally enter the correct text, and click **Compare both OCR engines**. Both read the same crop locally. Uncheck **Enhance crop for comparison** to test the original pixels. The table separates OCR time from model preparation and shows exact match and character error rate when reference text is provided. Confidence scores from different engines are not interchangeable. The standard plate-reading flow continues to use EasyOCR.

The optional API is `POST /plate/compare` with multipart fields `image`, `plate_bbox`, optional `expected`, optional `camera`, and optional `enhance` (default `true`). Each engine returns its own text, status, error, timing, and optional reference score. A missing optional model is reported as an engine error without preventing the EasyOCR result.

For repeatable raw-versus-enhanced measurements, place the supplied photos in `data/samples/ocr_benchmark/` under the filenames in `data/ocr_comparison_labels.csv`, then run:

```bash
python scripts/benchmark_ocr.py --repeats 5
```

See [measured results](reports/ocr_comparison.md), [per-sample timings and outputs](reports/ocr_comparison.json), and the [plate-box proposal](reports/plate_localization_proposal.md). The benchmark uses the original OpenThai **2.0.0** release, not the 2.1 version now served by the old unpinned model URL. Both engines use one CPU inference thread and alternate execution order. The photos and downloaded weights stay local and are ignored by Git.

## Test and evaluate

```bash
python -m pytest -q
python scripts/evaluate.py
```

Place consented test images in `data/samples/` and their ground truth in [data/labels.csv](data/labels.csv). The evaluation records classification accuracy, exact plate matches, review rate, and processing latency. See the [evaluation template](reports/day1_evaluation.md). The sample response and report template contain illustrative values.
