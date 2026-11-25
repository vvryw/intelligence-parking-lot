# Intelligence Parking Lot

Intelligence Parking Lot is a team project that combines parking sensors, license plate recognition, and barrier control. The local laptop coordinates entry and exit. It captures an image, validates the plate and vehicle type, and decides whether to open the barrier. The cloud stores the image and parking record in the background after the laptop sends the open command.

## Repository scope

This repository covers the **ESP32-CAM and the local camera and recognition service**. The service accepts a JPEG or takes a fresh still from the ESP32-CAM, identifies the vehicle, reads the plate, and returns structured results for parking decisions. The repository also includes a CLI, example responses, tests, and an evaluation script.

The **team dashboard and cloud server are separate team components**. They provide the dashboard for all sensor data and the server that updates the cloud. The remaining project work is to connect them to this repository's camera and recognition service and test a full parking event.

## Project status

| Component | Current status |
| --- | --- |
| Team dashboard and cloud server | A teammate has completed the sensor dashboard and cloud update server. The camera and parking event handoff still needs end-to-end verification. |
| Camera and recognition service on GitHub | The published prototype processes a local image or a configured camera URL. It provides the CLI and FastAPI endpoints for classification and plate OCR using Gemini. |
| Newer local camera prototype | The working copy includes ESP32-CAM firmware and setup, a live preview and simulated sensor alert, automatic plate crop suggestions with manual correction, and local Thai and English OCR with EasyOCR. Gemini still handles vehicle classification and suggests the plate crop. These updates are in the local working copy pending a separate code commit. |
| Automatic parking operation | Sensor triggers, parking decisions, servo commands, and background cloud archiving still need to be connected and tested together. |

The local prototype currently returns `car` (including sports cars), `supercar`, or `unknown`. The parking system needs a reliable `car` versus `motorcycle` result for bays A1 and M1. Motorcycle classification is still to do.

## How the full system works

| Component | Role in the integrated system |
| --- | --- |
| Arduino UNO R3 | Reads the A1 and M1 ultrasonic sensors and drives the barrier servo. A dedicated entrance detector or an operator action supplies the arrival trigger. |
| ESP32 DevKit gateway | Carries occupancy and arrival events from the UNO to the local server and sends gate commands back to the UNO. It also handles its own sensor readings. |
| ESP32-CAM | Sends one still image directly to the local server over Wi-Fi. |
| Local laptop or on-site server | Requests the image, obtains and validates plate text and vehicle type, checks the matching bay, and sends the gate command. |
| Team dashboard and cloud server | Show sensor and parking status and receive the image and event record for storage. |

1. For automatic capture at the gate, an entrance detector sends an arrival event through the UNO and ESP32 gateway. The A1 and M1 sensors track occupancy inside the bays.
2. The local laptop requests one JPEG from the ESP32-CAM. It obtains the plate text and vehicle type for that same arrival.
3. On entry, the laptop validates the result and reserves a matching available bay. On exit, it checks the active parking record for the plate. Unclear or failed recognition goes to operator review.
4. The laptop sends a small `OPEN` command through the ESP32 gateway to the UNO, which drives the servo. The laptop then syncs the image and parking record to the team's cloud server in the background.

With the current recognition pipeline, the Gemini API call still happens before the gate opens. The permanent image archive can finish later. Running both classification and OCR locally would allow the gate decision to run without an internet connection.

## What to do next

1. **Connect the existing team services.** Agree on the camera and parking event endpoints, event ID, and fields for sensor status, vehicle type, plate text, bay, review status, and the archived image.
2. **Connect the arrival trigger.** Verify the UNO-to-gateway link, keep A1 and M1 reporting occupancy, and wire up one gate capture per arrival. Use the existing simulated alert to test the camera flow before connecting hardware.
3. **Complete recognition for parking.** Add motorcycles to the supported classes and evaluate both vehicle types and Thai plate OCR with real ESP32-CAM frames. Route unclear readings to operator review.
4. **Connect the gate decision and cloud sync.** Reserve a matching entry bay or validate the exit plate before issuing `OPEN`. Confirm command receipt, then queue the image and record for background upload. Reuse the event ID for upload retries.
5. **Test across the whole system.** Verify entry, exit, a full matching bay, unreadable plates, repeated arrival messages, and a failed cloud archive upload. Measure the time from detection to the gate command and verify that the dashboard and cloud record show the same event.

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

Set `GEMINI_API_KEY` in `.env` and choose a `GEMINI_MODEL` available to your project. For camera input, set `ESP32_CAPTURE_URL` to the camera's still-image endpoint, such as `http://192.168.1.50/capture`. Put the camera and laptop on the same reachable network. See the [camera setup guide](camera/README.md).

```bash
# Replace this path with a local test image.
python -m app.cli --image data/samples/vehicle.jpg

# Or take a still using ESP32_CAPTURE_URL.
python -m app.cli --camera-url

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [the API docs](http://127.0.0.1:8000/docs) to try `POST /infer` with an uploaded image or `POST /capture` with the configured camera. Both accept an optional `plate_bbox` in original-image pixel coordinates, `x1,y1,x2,y2`. The result includes the vehicle class, recognized plate, review status, and processing times. See the [example response](examples/sample_response.json).

### Newer local camera test

These steps apply once the newer local camera and EasyOCR changes are available. Flash the AI Thinker-compatible sketch in `camera/parking_camera/` using the [camera setup guide](camera/README.md). With the ESP32-CAM-MB connected by USB, run `python -m app.camera_setup` to find the camera, save a test frame, and set its URL.

Start the local server and open [the camera test page](http://127.0.0.1:8000/). Click **Simulate sensor alert** to pause the preview and take a fresh photo. The page shows the vehicle class, the enhanced plate crop, the OCR result, and timings. If the crop is wrong, draw a box around the plate and click **Read selected plate**. EasyOCR downloads its Thai and English weights on first use and reuses them locally afterward.

## Test and evaluate

```bash
python -m pytest -q
python scripts/evaluate.py
```

Place consented test images in `data/samples/` and their ground truth in [data/labels.csv](data/labels.csv). The evaluation records classification accuracy, exact plate matches, review rate, and processing latency. See the [evaluation template](reports/day1_evaluation.md). The sample response and report template contain illustrative values.
