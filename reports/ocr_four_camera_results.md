# Measured OCR comparison: easyocr, openthai-2.0.0, paddleocr-v5-th, paddleocr-v5-en

4 supplied photos; 4 manually labeled crops; 10 warm reads per engine/crop/variant.
Manual boxes isolate registration characters, province names, and English labels. This measures OCR given a correct crop; it does not measure automatic plate detection.
Registration references were transcribed visually before running OCR and subsequently confirmed by the user: จม835, ME204, and AB385. Crops of the same physical plate are correlated, not independent plates.

All engines run locally on this Mac with one CPU inference thread each, sequentially with alternating order. No camera, network, Gemini, or image-file loading time is included in OCR latency. Enhancement runs once per crop and is recorded separately in JSON. Engines retain their own decoding and internal filters. PP-OCRv5 uses its mobile detector at maximum side 736 and no orientation classifier for these upright crops. This includes text detection plus recognition, not registration-plate localization.
The English specialist is evaluated only on samples declared English in the manifest. This is a configured language profile, not an automatic Thai/English router; its result must not be represented as multilingual accuracy.
Exact match and CER use the first warm output for each crop; repeated reads measure latency, not additional independent accuracy samples. Spaces and punctuation are removed, Latin case is ignored, Unicode NFC is applied, and Thai marks are preserved. CER is edit distance divided by reference character count; lower is better. Confidence scores are not calibrated across engines.

| Text group | Input | Engine | Exact matches | CER | Warm median | Warm p95 |
|---|---|---|---:|---:|---:|---:|
| english-registration | raw | easyocr | 0/3 | 53.3% | 150.5 ms | 208.8 ms |
| english-registration | raw | openthai-2.0.0 | 0/3 | 66.7% | 100.1 ms | 231.0 ms |
| english-registration | raw | paddleocr-v5-th | 1/3 | 53.3% | 32.2 ms | 41.8 ms |
| english-registration | raw | paddleocr-v5-en | 1/3 | 46.7% | 32.7 ms | 41.6 ms |
| english-registration | enhanced | easyocr | 0/3 | 60.0% | 333.7 ms | 474.9 ms |
| english-registration | enhanced | openthai-2.0.0 | 0/3 | 53.3% | 117.0 ms | 204.4 ms |
| english-registration | enhanced | paddleocr-v5-th | 2/3 | 33.3% | 44.4 ms | 53.3 ms |
| english-registration | enhanced | paddleocr-v5-en | 2/3 | 33.3% | 44.1 ms | 55.1 ms |
| thai-registration | raw | easyocr | 1/1 | 0.0% | 136.0 ms | 166.2 ms |
| thai-registration | raw | openthai-2.0.0 | 0/1 | 40.0% | 63.5 ms | 71.9 ms |
| thai-registration | raw | paddleocr-v5-th | 1/1 | 0.0% | 31.1 ms | 81.6 ms |
| thai-registration | enhanced | easyocr | 0/1 | 60.0% | 353.6 ms | 587.5 ms |
| thai-registration | enhanced | openthai-2.0.0 | 0/1 | 20.0% | 124.2 ms | 169.2 ms |
| thai-registration | enhanced | paddleocr-v5-th | 1/1 | 0.0% | 50.6 ms | 62.1 ms |

## Model startup

| Engine | Import/weights/session ready | First inference |
|---|---:|---:|
| easyocr | 4213.4 ms | 211.4 ms |
| openthai-2.0.0 | 185.0 ms | 66.4 ms |
| paddleocr-v5-th | 318.1 ms | 31.5 ms |
| paddleocr-v5-en | 56.7 ms | 29.2 ms |

These are fresh reader instances in this benchmark process, not a guaranteed cold filesystem cache or total Python process startup. Downloads are excluded.

## Predictions

| Crop | Expected | Input | easyocr | openthai-2.0.0 | paddleocr-v5-th | paddleocr-v5-en |
|---|---|---|---|---|---|---|
| red_thai | จม835 | raw | จม835 | 835 | จม835 | not applicable |
| red_thai | จม835 | enhanced | จมอวร | จุม835 | จม835 | not applicable |
| black_english | ME204 | raw | me20 | ME2O4 | ME204 | ME204 |
| black_english | ME204 | enhanced | mะ20 | ME04 | ME204 | ME204 |
| yellow_near | AB385 | raw | งาร5 | 3๖ | 483S5 | 48385 |
| yellow_near | AB385 | enhanced | งาร | ∅ | ∅ | ∅ |
| yellow_far | AB385 | raw | a88 | ∅ | ∅ | ∅ |
| yellow_far | AB385 | enhanced | a8345 | AB3S | AB385 | AB385 |

## Scope and reproducibility

This is a small, hand-selected set of four screenshots showing three physical plates. The two yellow-car shots show the same AB385 label, so they are correlated. All reference labels have been confirmed by the user. These results do not establish production accuracy; test more physical plates and held-out entry/exit frames before deployment.

OpenThai release: `v2.0.0`, commit `0fca92b87621f063f22c8de4d91460fb79f0e207`. The old model URL redirects to 2.1; the setup script pins the requested release and verifies file checksums.

```bash
python -m pip install -r requirements-ocr-compare.txt
python scripts/setup_openthai.py
python scripts/setup_paddleocr.py
python scripts/benchmark_ocr.py --labels data/ocr_four_camera_labels.csv --images data/samples/ocr_four_camera_test --paddle --english-specialist --repeats 10 --output reports/ocr_four_camera_results.json --report reports/ocr_four_camera_results.md
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
    "numpy": "2.4.6",
    "rapidocr": "3.10.0"
  },
  "openthai_revision": "0fca92b87621f063f22c8de4d91460fb79f0e207",
  "inference_threads_per_engine": 1,
  "repeats": 10,
  "original_photos": 4,
  "labeled_crops": 4,
  "model_startup": {
    "easyocr": {
      "model_ready_ms": 4213.372165992041,
      "first_inference_ms": 211.39374999620486
    },
    "openthai-2.0.0": {
      "model_ready_ms": 184.9947920127306,
      "first_inference_ms": 66.43420799809974
    },
    "paddleocr-v5-th": {
      "model_ready_ms": 318.1180410028901,
      "first_inference_ms": 31.49929099890869
    },
    "paddleocr-v5-en": {
      "model_ready_ms": 56.717291998211294,
      "first_inference_ms": 29.162582999560982
    }
  },
  "sample_ids": [
    "red_thai",
    "black_english",
    "yellow_near",
    "yellow_far"
  ],
  "preprocessing_source_sha256": "ef57ce007f6c5c231303236a05e0d81e1e91d6ce9bf46105cb7ae381f3673cb4",
  "manual_boxes": true,
  "paddle_models": {
    "release": "RapidOCR v3.10.0",
    "base": "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/",
    "sha256": {
      "det/ch_PP-OCRv5_det_mobile.onnx": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
      "rec/th_PP-OCRv5_rec_mobile.onnx": "de541dd83161c241ff426f7ecfd602a0ba77d686cf3ab9a6c255ea82fd08006e",
      "rec/en_PP-OCRv5_rec_mobile.onnx": "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8"
    }
  },
  "paddle_config": {
    "detector": "PP-OCRv5 mobile",
    "det_limit": "max 736",
    "orientation_classifier": false,
    "runtime": "ONNX CPU"
  }
}
```
