# OCR findings and recommendation

The main benchmark used the first six supplied photos and 19 manually labeled crops. A separate focused follow-up used the seventh photo (the second camera screenshot). Across both runs there were 400 warm OCR reads. Repeated reads measure timing; they are not independent accuracy samples.

| Text | Input | Engine | Exact | Median OCR | p95 OCR |
|---|---|---|---:|---:|---:|
| english-label | raw | easyocr | 0/6 | 349.3 ms | 604.0 ms |
| english-label | raw | openthai-2.0.0 | 1/6 | 49.8 ms | 53.8 ms |
| numeric-registration | raw | easyocr | 1/2 | 120.8 ms | 129.6 ms |
| numeric-registration | raw | openthai-2.0.0 | 2/2 | 49.1 ms | 56.7 ms |
| thai-province | raw | easyocr | 4/4 | 115.5 ms | 141.2 ms |
| thai-province | raw | openthai-2.0.0 | 3/4 | 19.7 ms | 40.5 ms |
| thai-registration | enhanced | easyocr | 4/6 | 507.8 ms | 560.1 ms |
| thai-registration | enhanced | openthai-2.0.0 | 2/6 | 99.8 ms | 115.7 ms |

On these examples, EasyOCR in Thai+English mode reads more Thai registrations correctly, especially after enhancement. OpenThai OCR Text 2.0.0 is faster and reads both numeric-only registrations correctly. Neither is reliable on the English labels: OpenThai has more exact words (1/6 versus 0/6), while raw EasyOCR has lower aggregate character error rate (26.1% versus 37.0%). Those different metrics do not support a broad English accuracy winner.

Keep EasyOCR as the current Thai-plate baseline and expose OpenThai for comparison. Do not auto-switch engines using their confidence numbers: these scores have different meanings and are not cross-calibrated. Test a larger held-out sample before choosing a production default.

Enhancement is conditional. It improves Thai-registration exact matches for EasyOCR from 3/6 to 4/6, but reduces its province accuracy from 4/4 to 2/4 and numeric registration accuracy from 1/2 to 0/2. The comparison page therefore offers both raw and enhanced input.

Both camera screenshots fail to reproduce the provisional AB385 reference with either engine, including a manually correct registration crop. The latest screenshot is darker and blurred. Improve capture lighting, focus and motion stability, and evaluate the original snapshot JPEG rather than a screen capture. Do not treat an OCR guess from either image as a verified plate.

The first screenshot's combined Gemini vehicle-classification/plate-box request took 66.2 seconds and found the paper plate, with IoU 0.617 against the tighter manual registration-row box. The OpenCV rectangle heuristic took about 7.8 ms but selected a background object (IoU 0). These are single-image results; this is not a detector benchmark or a typical Gemini latency estimate.

The second screenshot's Gemini request took 3.0 seconds and found the paper plate, with IoU 0.627 against its manual registration row. The heuristic again missed (IoU 0). These two timings show substantial variability, so a larger latency sample is needed before quoting a typical Gemini response time.

The proposed production flow is local plate detection, registration-row selection, optional rectification and enhancement, then local OCR, alongside Gemini classification. See plate_localization_proposal.md.

Artifacts: ocr_comparison.md/json; ocr_camera_followup.md/json; plate_bbox_test.json; plate_bbox_followup.json; camera_ocr_manual.json; camera_ocr_gemini.json. All measurements exclude network/camera/Gemini time unless explicitly stated otherwise.
