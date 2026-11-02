# Day 1 Gemini service evaluation template

> Add real, authorized ESP32-CAM examples before recording results. Do not replace
> Gemini failures with manual corrections in the measured output.

## Environment

- Date / operator:
- Python and `google-genai` version:
- Gemini model and region/project configuration:
- ESP32-CAM board / resolution / JPEG quality:
- Capture distance and lighting:
- Does latency include camera/network capture? (yes/no):

## Quantitative results

- Images and class distribution (`car`, `sportcar`, `supercar`, no/unknown):
- Vehicle accuracy (correct / applicable labeled):
- Vehicle `unknown` / review count:
- Plate exact-match accuracy (exact normalized / readable labeled):
- Plate review/failure rate:
- Mean / median / p95 Gemini vehicle-service latency:
- Mean / median / p95 plate-and-Gemini-OCR latency:
- Mean end-to-end processing latency and service cost/usage if available:
- Sequential processing FPS (`1000 / total_ms`; excludes capture unless stated):

## Qualitative cases

| Condition | File | Ground truth | Gemini output | Success/failure reason |
|---|---|---|---|---|
| Normal | | | | |
| Dark | | | | |
| Blurry | | | | |
| Angled | | | | |
| Occluded | | | | |
| No vehicle | | | | |
| Multiple vehicles | | | | |

## Manual findings

- Camera/Wi-Fi observations:
- Cases needing manual plate bbox:
- Gemini model/version limitations:
- Privacy/authorization review:

