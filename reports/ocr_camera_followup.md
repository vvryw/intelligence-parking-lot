# Measured OCR comparison: EasyOCR and OpenThai OCR Text 2.0.0

1 supplied photos; 1 manually labeled crops; 5 warm reads per engine/crop/variant.
Manual boxes isolate registration characters, province names, and English labels. This measures OCR given a correct crop; it does not measure automatic plate detection.
English labels on Thai plates are scored separately from the blurred camera screenshot's provisional English registration reference AB385. Registration references were transcribed visually before running OCR.

Both engines run locally on this Mac with one CPU inference thread each, sequentially with alternating order. No camera, network, Gemini, or image-file loading time is included in OCR latency. Enhancement runs once per crop and is recorded separately in JSON. Engines retain their own default decoding and internal filters.
Exact match and CER use the first warm output for each crop; repeated reads measure latency, not additional independent accuracy samples. Spaces and punctuation are removed, Latin case is ignored, Unicode NFC is applied, and Thai marks are preserved. CER is edit distance divided by reference character count; lower is better. Confidence scores are not calibrated across engines.

| Text group | Input | Engine | Exact matches | CER | Warm median | Warm p95 |
|---|---|---|---:|---:|---:|---:|
| english-camera-registration | raw | easyocr | 0/1 | 100.0% | 266.5 ms | 343.1 ms |
| english-camera-registration | raw | openthai-2.0.0 | 0/1 | 100.0% | 128.7 ms | 150.9 ms |
| english-camera-registration | enhanced | easyocr | 0/1 | 100.0% | 258.4 ms | 301.6 ms |
| english-camera-registration | enhanced | openthai-2.0.0 | 0/1 | 100.0% | 112.7 ms | 114.3 ms |

## Model startup

| Engine | Import/weights/session ready | First inference |
|---|---:|---:|
| easyocr | 4616.3 ms | 340.2 ms |
| openthai-2.0.0 | 67.6 ms | 131.9 ms |

These are fresh reader instances in this benchmark process, not a guaranteed cold filesystem cache or total Python process startup. Downloads are excluded.

## Predictions

| Crop | Expected | Input | EasyOCR | OpenThai |
|---|---|---|---|---|
| english_camera_07 | AB385 | raw | มะ | ∅ |
| english_camera_07 | AB385 | enhanced | ง | ∅ |

## Scope and reproducibility

This is a small, hand-selected set, with several crops from the same photo. It does not establish production accuracy or ESP32-CAM performance. Colored souvenir plates and repeated English words further limit generalization. Test held-out real entry/exit frames with known registration strings before changing the default engine.

OpenThai release: `v2.0.0`, commit `0fca92b87621f063f22c8de4d91460fb79f0e207`. The old model URL redirects to 2.1; the setup script pins the requested release and verifies file checksums.

```bash
python -m pip install -r requirements-ocr-compare.txt
python scripts/setup_openthai.py
python scripts/benchmark_ocr.py
```

```json
{
  "platform": "macOS-26.6.2-arm64-arm-64bit",
  "machine": "arm64",
  "python": "3.11.14",
  "versions": {
    "easyocr": "1.7.2",
    "torch": "2.15.0",
    "onnxruntime": "1.32.0",
    "numpy": "2.4.6"
  },
  "openthai_revision": "0fca92b87621f063f22c8de4d91460fb79f0e207",
  "inference_threads_per_engine": 1,
  "repeats": 5,
  "original_photos": 1,
  "labeled_crops": 1,
  "model_startup": {
    "easyocr": {
      "model_ready_ms": 4616.286332995514,
      "first_inference_ms": 340.20658399822423
    },
    "openthai-2.0.0": {
      "model_ready_ms": 67.58445800369373,
      "first_inference_ms": 131.898708998051
    }
  }
}
```
