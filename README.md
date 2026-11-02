# Intelligence Parking Lot — Gemini AI service prototype

This laptop/server prototype processes one still image using the Google Gemini API.
It classifies the dominant vehicle as `car`, `sportcar`, `supercar`, or `unknown`,
then sends a locally cropped/preprocessed license plate to Gemini for OCR. It does
not implement parking allocation, gate/servo control, Arduino integration, MQTT,
a dashboard, or persistence.

## Components

- `app/gemini_service.py` is the shared official `google-genai` SDK wrapper. It
  sends an image with a JSON schema and validates the returned JSON.
- `app/vehicle_detector.py` uses Gemini vision to return one vehicle class,
  service-provided confidence, and an optional bounding box. Gemini confidence is
  not calibrated; low-confidence and `unknown` outputs require review.
- `app/plate_pipeline.py` remains local OpenCV preprocessing. A manual original
  image bbox is supported because no dedicated plate-detector is in this scope.
- `app/ocr_reader.py` sends that crop to Gemini OCR. It preserves Thai letters,
  digits, and Thai combining marks; it never fills in unreadable text.
- `app/pipeline.py`, `app/main.py`, and `app/cli.py` assemble the JSON result.

All bounding boxes use `[x_min, y_min, x_max, y_max]` in original-image pixels.

## Setup

Use Python 3.11 or newer:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Get an API key through Google AI Studio and put it in the local `.env` file
(automatically loaded), or set it for the current PowerShell session:

```powershell
# Alternative to setting GEMINI_API_KEY in .env:
$env:GEMINI_API_KEY = "your-key"
$env:GEMINI_MODEL = "gemini-3.8-flash"
```

Use a model available to your Gemini project; `GEMINI_MODEL` is configurable.
Do not commit API keys or real plate images. Gemini receives the full image for
vehicle classification and the plate crop for OCR, so obtain authorization before
uploading identifying images.

## Run

```powershell
# Local image; manual bbox is recommended for controlled tests.
python -m app.cli --image .\data\samples\car_01.jpg --plate-bbox 336,600,680,712

# HTTP API
uvicorn app.main:app --host 127.0.0.1 --port 8000
curl.exe -X POST http://127.0.0.1:8000/infer `
  -F "image=@data/samples/car_01.jpg" `
  -F "plate_bbox=336,600,680,712"
```

`POST /capture` uses the configured `ESP32_CAPTURE_URL`; see
[camera setup](camera/README.md). Camera connectivity, focus/exposure, Gemini
model availability, response latency/cost, and Thai plate accuracy require manual
testing on approved ESP32-CAM images.

## Test and evaluate

```powershell
pytest -q
python scripts/evaluate.py
```

Add consented images under `data/samples/` and ground truth to `data/labels.csv`.
The evaluation output distinguishes `unknown` vehicle responses, exact normalized
plate matches, review rate, latency, and sequential processing FPS. It does not
claim camera-frame-rate FPS.

See the [evaluation template](reports/day1_evaluation.md) and the illustrative
[response](examples/sample_response.json). Example values are not measurements.

