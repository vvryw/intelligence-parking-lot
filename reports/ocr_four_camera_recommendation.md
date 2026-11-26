# Four-camera-image OCR recommendation

PaddleOCR **PP-OCRv5 mobile Thai** is the strongest new candidate on this supplied set. The recognizer supports Thai, English, and numbers, and runs on this Mac through ONNX Runtime/RapidOCR 3.10.0. This is a candidate for larger validation, not a production accuracy claim.

The four screenshots show three physical registration labels: `จม835` on the red car, `ME204` on the black car, and `AB385` in two yellow-car views. The references were read visually before OCR and subsequently confirmed by the user. The test uses one correct manual crop per screenshot. Two views of the same AB385 label are correlated. Model confidence is not used as ground truth.

## Measured comparison

All models read identical inputs on one CPU inference thread. Each image/variant/model has ten warm calls, run sequentially with alternating model order. There are 300 timed reads, including the separate English-only Paddle profile. Repetition measures latency stability, not additional accuracy samples. No engine failures occurred and all repeated text outputs were consistent.

| Model | Raw exact matches | Raw median / p95 | Enhanced exact matches | Enhanced median / p95 |
|---|---:|---:|---:|---:|
| EasyOCR Thai+English | 1/4 | 145.5 / 206.0 ms | 0/4 | 336.2 / 529.9 ms |
| OpenThai OCR Text 2.0.0 | 0/4 | 95.7 / 226.8 ms | 0/4 | 120.9 / 202.0 ms |
| **PP-OCRv5 mobile Thai+English** | **2/4** | **31.6 / 42.3 ms** | **3/4** | **45.5 / 61.9 ms** |
| PP-OCRv5 English-only specialist | 1/3 English | 32.7 / 41.6 ms | 2/3 English | 44.1 / 55.1 ms |

Times include text detection and recognition inside the selected crop, but exclude finding the license plate in the full photograph, enhancement, file loading, model loading, and network/camera time. The PP-OCRv5 detector is capped at longest side 736 with orientation classification disabled for these upright inputs. Other engines retain their existing settings. Consequently this compares the tested deployment configurations, not model architecture alone. The English-only specialist is selected using the declared language of each sample; it is not an automatic language router and cannot read Thai.

## PP-OCRv5 Thai-model predictions

| Screenshot | Reference | Raw output | Enhanced output | Enhanced median OCR |
|---|---|---|---|---:|
| Red car | จม835 | จม835 | จม835 | 50.6 ms |
| Black car | ME204 | ME204 | ME204 | 44.6 ms |
| Yellow car, closer | AB385 | 483S5 | no text | 35.9 ms |
| Yellow car, farther | AB385 | no text | AB385 | 46.9 ms |

The nearer yellow-car image remains a failure. Its raw wrong output has confidence 0.70, illustrating why a confidence threshold alone cannot establish a verified registration. Do not silently map the model's `4` to `A` or choose a processing variant using the known answer. The English specialist did not improve exact accuracy on these three English views.

## Recommended next step

PP-OCRv5 mobile Thai+English is now the selected default for normal camera, API, and CLI plate reads, following the user's decision on November 27. It remains available on the comparison page. Preserve a correct plate/registration-row crop, keep both raw and enhanced pixels available, and send uncertain or conflicting readings to review. The new image enhancement can help the distant AB385 view while still failing on the near view; it is not a guaranteed repair for blur.

Before production gate use, validate more physical plates and held-out frames, including additional Thai labels, camera distances, lighting, and motion. Evaluate plate localization separately. Neither this four-image test nor a correct manual crop establishes end-to-end gate reliability.

Artifacts: `ocr_four_camera_results.md`, `ocr_four_camera_results.json`, `data/ocr_four_camera_labels.csv`, and the ignored local image folder `data/samples/ocr_four_camera_test/`. Setup uses SHA-256-verified ONNX models from RapidAI's pinned v3.10.0 model manifest. The benchmark JSON records model hashes, package versions, platform, and current enhancement-source hash. Enhancement changed since the previous day's run, so comparisons here rerun every engine using the same current code rather than reusing older measurements.

Sources: [PaddleOCR multilingual model support](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.en.md), [RapidOCR 3.10.0 model manifest](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/rapidocr/default_models.yaml), [RapidOCR 3.10.0 parameters](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/rapidocr/config.yaml).
