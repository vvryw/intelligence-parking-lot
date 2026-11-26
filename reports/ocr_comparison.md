# Measured OCR comparison: EasyOCR and OpenThai OCR Text 2.0.0

6 supplied photos; 19 manually labeled crops; 5 warm reads per engine/crop/variant.
Manual boxes isolate registration characters, province names, and English labels. This measures OCR given a correct crop; it does not measure automatic plate detection.
English labels on Thai plates are scored separately from the blurred camera screenshot's provisional English registration reference AB385. Registration references were transcribed visually before running OCR.

Both engines run locally on this Mac with one CPU inference thread each, sequentially with alternating order. No camera, network, Gemini, or image-file loading time is included in OCR latency. Enhancement runs once per crop and is recorded separately in JSON. Engines retain their own default decoding and internal filters.
Exact match and CER use the first warm output for each crop; repeated reads measure latency, not additional independent accuracy samples. Spaces and punctuation are removed, Latin case is ignored, Unicode NFC is applied, and Thai marks are preserved. CER is edit distance divided by reference character count; lower is better. Confidence scores are not calibrated across engines.

| Text group | Input | Engine | Exact matches | CER | Warm median | Warm p95 |
|---|---|---|---:|---:|---:|---:|
| english-camera-registration | raw | easyocr | 0/1 | 60.0% | 135.0 ms | 136.7 ms |
| english-camera-registration | raw | openthai-2.0.0 | 0/1 | 100.0% | 96.3 ms | 96.8 ms |
| english-camera-registration | enhanced | easyocr | 0/1 | 60.0% | 219.8 ms | 220.9 ms |
| english-camera-registration | enhanced | openthai-2.0.0 | 0/1 | 100.0% | 97.5 ms | 97.7 ms |
| english-label | raw | easyocr | 0/6 | 26.1% | 349.3 ms | 604.0 ms |
| english-label | raw | openthai-2.0.0 | 1/6 | 37.0% | 49.8 ms | 53.8 ms |
| english-label | enhanced | easyocr | 0/6 | 32.6% | 356.8 ms | 602.1 ms |
| english-label | enhanced | openthai-2.0.0 | 1/6 | 28.3% | 52.9 ms | 64.2 ms |
| numeric-registration | raw | easyocr | 1/2 | 8.3% | 120.8 ms | 129.6 ms |
| numeric-registration | raw | openthai-2.0.0 | 2/2 | 0.0% | 49.1 ms | 56.7 ms |
| numeric-registration | enhanced | easyocr | 0/2 | 16.7% | 226.2 ms | 257.6 ms |
| numeric-registration | enhanced | openthai-2.0.0 | 2/2 | 0.0% | 89.2 ms | 91.8 ms |
| thai-province | raw | easyocr | 4/4 | 0.0% | 115.5 ms | 141.2 ms |
| thai-province | raw | openthai-2.0.0 | 3/4 | 2.6% | 19.7 ms | 40.5 ms |
| thai-province | enhanced | easyocr | 2/4 | 15.4% | 259.4 ms | 328.4 ms |
| thai-province | enhanced | openthai-2.0.0 | 2/4 | 5.1% | 97.6 ms | 109.3 ms |
| thai-registration | raw | easyocr | 3/6 | 12.5% | 530.7 ms | 714.9 ms |
| thai-registration | raw | openthai-2.0.0 | 2/6 | 18.8% | 105.2 ms | 141.1 ms |
| thai-registration | enhanced | easyocr | 4/6 | 6.2% | 507.8 ms | 560.1 ms |
| thai-registration | enhanced | openthai-2.0.0 | 2/6 | 40.6% | 99.8 ms | 115.7 ms |

## Model startup

| Engine | Import/weights/session ready | First inference |
|---|---:|---:|
| easyocr | 3717.8 ms | 149.4 ms |
| openthai-2.0.0 | 170.4 ms | 50.7 ms |

These are fresh reader instances in this benchmark process, not a guaranteed cold filesystem cache or total Python process startup. Downloads are excluded.

## Predictions

| Crop | Expected | Input | EasyOCR | OpenThai |
|---|---|---|---|---|
| thai_reg_01 | กห6000 | raw | กห6000 | 6000 |
| thai_reg_01 | กห6000 | enhanced | กห6000 | 6000 |
| numeric_reg_02 | 90-9397 | raw | 909397 | 909397 |
| numeric_reg_02 | 90-9397 | enhanced | 909307 | 909397 |
| numeric_reg_03 | 70-2157 | raw | 70257 | 702157 |
| numeric_reg_03 | 70-2157 | enhanced | 70257 | 702157 |
| thai_reg_04 | กย8756 | raw | กย8756 | กย8756 |
| thai_reg_04 | กย8756 | enhanced | กย8756 | กย8756 |
| thai_reg_05_yellow | ส1111 | raw | ส1111 | ส1111 |
| thai_reg_05_yellow | ส1111 | enhanced | ส1111 | ส1111 |
| thai_reg_05_white | ส6767 | raw | สะ6767 | สี6767 |
| thai_reg_05_white | ส6767 | enhanced | สะ6767 | สี6767 |
| thai_reg_05_green | ส1111 | raw | สี1111 | สAT11 |
| thai_reg_05_green | ส1111 | enhanced | สี1111 | ∅ |
| thai_reg_05_red | ส6969 | raw | สีะ6969 | ล6969 |
| thai_reg_05_red | ส6969 | enhanced | ส6969 | ∅ |
| english_02 | THAILAND | raw | thallano | THAILAND |
| english_02 | THAILAND | enhanced | กหิaานaาd | THAILAND |
| english_03 | THAILAND | raw | thalland | THAILAMD |
| english_03 | THAILAND | enhanced | thalland | THAITLAND |
| english_05_yellow | BANGKOK | raw | banckok | ∅ |
| english_05_yellow | BANGKOK | enhanced | banckok | ∅ |
| english_05_white | THAILAND | raw | thalland | THA1LAND |
| english_05_white | THAILAND | enhanced | thalland | THA1LAND |
| english_05_green | BANGKOK | raw | banckok | ∅ |
| english_05_green | BANGKOK | enhanced | banckok | ANCEOK |
| english_05_red | THAILAND | raw | สรบnd | THALAND |
| english_05_red | THAILAND | enhanced | สพand | THA1LAND |
| province_01 | เชียงใหม่ | raw | เชียงใหม่ | เชียงใหม่ |
| province_01 | เชียงใหม่ | enhanced | เขียงใหม่ | เชียงใหม่ |
| province_02 | กรุงเทพมหานคร | raw | กรุงเทพมหานคร | กรุงเหพมหานคร |
| province_02 | กรุงเทพมหานคร | enhanced | คระเทพมาาจนคร | กรุงเหพมหานคร |
| province_03 | อุดรธานี | raw | อุดรธานี | อุดรธานี |
| province_03 | อุดรธานี | enhanced | อุดรธานี | อุดรธานี |
| province_04 | เชียงใหม่ | raw | เชียงใหม่ | เชียงใหม่ |
| province_04 | เชียงใหม่ | enhanced | เชียงใหม่ | เชียงใทม่ |
| english_camera_06 | AB385 | raw | a88 | ∅ |
| english_camera_06 | AB385 | enhanced | 838 | ∅ |

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
  "original_photos": 6,
  "labeled_crops": 19,
  "model_startup": {
    "easyocr": {
      "model_ready_ms": 3717.77441699669,
      "first_inference_ms": 149.39404200413264
    },
    "openthai-2.0.0": {
      "model_ready_ms": 170.43741599627538,
      "first_inference_ms": 50.68274999939604
    }
  }
}
```
