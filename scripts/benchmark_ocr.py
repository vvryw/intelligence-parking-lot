"""Compare local OCR engines on identical labeled crops; no Gemini calls."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import shlex
import sys
from collections import defaultdict
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import torch

from app.ocr_comparison import score_text
from app.ocr_reader import EasyOCRReader
from app.openthai_reader import MODEL_REVISION, OpenThaiOCRReader
from app.plate_pipeline import crop_manual, preprocess_plate
from app.schemas import parse_bbox


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default="data/ocr_comparison_labels.csv")
    parser.add_argument("--images", default="data/samples/ocr_benchmark")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--only-ids", help="Comma-separated sample IDs for a focused follow-up test")
    parser.add_argument("--paddle", action="store_true", help="Include PP-OCRv5 mobile Thai+English")
    parser.add_argument("--english-specialist", action="store_true", help="Also test PP-OCRv5 English on declared English samples only")
    parser.add_argument("--output", default="reports/ocr_comparison.json")
    parser.add_argument("--report", default="reports/ocr_comparison.md")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    # Both engines use one CPU inference thread. Keep engines resident and run
    # sequentially, alternating order, rather than competing with each other.
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    cv2.setNumThreads(1)
    with Path(args.labels).open(encoding="utf-8-sig", newline="") as f:
        labels = list(csv.DictReader(f))
    if args.only_ids:
        ids = set(args.only_ids.split(","))
        labels = [sample for sample in labels if sample["id"] in ids]
        missing = ids - {sample["id"] for sample in labels}
        if missing:
            raise ValueError(f"Unknown sample IDs: {sorted(missing)}")
    if not labels:
        raise ValueError("The labels file contains no samples")
    readers = {"easyocr": EasyOCRReader(), "openthai-2.0.0": OpenThaiOCRReader()}
    if args.paddle or args.english_specialist:
        from app.paddleocr_reader import PaddleOCRReader
        if args.paddle:
            readers["paddleocr-v5-th"] = PaddleOCRReader("th")
        if args.english_specialist:
            readers["paddleocr-v5-en"] = PaddleOCRReader("en")
    ready = {}
    for engine, reader in readers.items():
        started = perf_counter()
        reader.warm_up()
        ready[engine] = {"model_ready_ms": (perf_counter() - started) * 1000}
        # Report the first inference separately; do not mix it with warm timing.
        sample = labels[0]
        image = cv2.imread(str(Path(args.images) / sample["filename"]))
        if image is None:
            raise ValueError(f"Missing image: {sample['filename']}")
        crop = crop_manual(image, parse_bbox(sample["bbox"]))
        first = reader.read(crop.image, crop.bbox)
        if first.status == "failed":
            raise RuntimeError(first.error)
        ready[engine]["first_inference_ms"] = first.processing_ms
        print(engine, "ready:", ready[engine], flush=True)
    rows = []
    for index, sample in enumerate(labels):
        image = cv2.imread(str(Path(args.images) / sample["filename"]))
        if image is None:
            raise ValueError(f"Missing image: {sample['filename']}")
        crop = crop_manual(image, parse_bbox(sample["bbox"]))
        if crop.image is None:
            raise ValueError(f"Invalid box: {sample['id']}: {crop.error}")
        for variant in ("raw", "enhanced"):
            started = perf_counter()
            prepared = crop.image if variant == "raw" else preprocess_plate(crop.image)
            preprocess_ms = (perf_counter() - started) * 1000
            measurements = defaultdict(list)
            for repeat in range(args.repeats):
                order = list(readers)
                if (index + repeat) % 2:
                    order.reverse()
                for engine in order:
                    if engine == "paddleocr-v5-en" and not sample["group"].startswith("english"):
                        continue
                    result = readers[engine].read(prepared, crop.bbox)
                    measurements[engine].append(asdict(result))
            for engine, results in measurements.items():
                first = results[0]
                rows.append({
                    **sample, "engine": engine, "variant": variant,
                    "input_width": crop.image.shape[1], "input_height": crop.image.shape[0],
                    "ocr_width": prepared.shape[1], "ocr_height": prepared.shape[0],
                    "preprocess_ms": preprocess_ms, "predicted": first["text"],
                    "score": score_text(sample["expected"], first["text"]),
                    "outputs_consistent": len({r["text"] for r in results}) == 1,
                    "failed_runs": sum(r["status"] == "failed" for r in results),
                    "latencies_ms": [r["processing_ms"] for r in results],
                    "status": first["status"], "confidence": first["confidence"], "error": first["error"],
                })
            print(sample["id"], variant, {engine: values[0]["text"] for engine, values in measurements.items()}, flush=True)
    summaries = []
    for group in sorted({r["group"] for r in rows}):
        for variant in ("raw", "enhanced"):
            for engine in readers:
                subset = [r for r in rows if (r["group"], r["variant"], r["engine"]) == (group, variant, engine)]
                if not subset:
                    continue
                latencies = [t for r in subset for t in r["latencies_ms"]]
                errors = sum(r["score"]["character_errors"] for r in subset)
                chars = sum(len(r["score"]["expected"]) for r in subset)
                summaries.append({
                    "group": group, "variant": variant, "engine": engine, "samples": len(subset),
                    "exact_matches": sum(r["score"]["exact_match"] for r in subset),
                    "cer": errors / chars, "median_ms": float(np.median(latencies)),
                    "p95_ms": float(np.percentile(latencies, 95)),
                    "failed_runs": sum(r["failed_runs"] for r in subset),
                })
    metadata = {
        "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
        "versions": {name: version(name) for name in ("easyocr", "torch", "onnxruntime", "numpy")},
        "openthai_revision": MODEL_REVISION, "inference_threads_per_engine": 1,
        "repeats": args.repeats, "original_photos": len({s["filename"] for s in labels}),
        "labeled_crops": len(labels), "model_startup": ready,
        "sample_ids": [sample["id"] for sample in labels],
        "preprocessing_source_sha256": hashlib.sha256((Path(__file__).resolve().parents[1] / "app/plate_pipeline.py").read_bytes()).hexdigest(),
        "manual_boxes": True,
    }
    if args.paddle or args.english_specialist:
        metadata["versions"]["rapidocr"] = version("rapidocr")
        from app.paddleocr_reader import PADDLE_MODEL_DIR
        metadata["paddle_models"] = json.loads((PADDLE_MODEL_DIR / "manifest.json").read_text())
        metadata["paddle_config"] = {"detector": "PP-OCRv5 mobile", "det_limit": "max 736", "orientation_classifier": False, "runtime": "ONNX CPU"}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"metadata": metadata, "summary": summaries, "samples": rows}, ensure_ascii=False, indent=2) + "\n")
    report = [
        "# Measured OCR comparison: " + ", ".join(readers), "",
        f"{metadata['original_photos']} supplied photos; {len(labels)} manually labeled crops; {args.repeats} warm reads per engine/crop/variant.",
        "Manual boxes isolate registration characters, province names, and English labels. This measures OCR given a correct crop; it does not measure automatic plate detection.",
        "Registration references were transcribed visually before running OCR. Any provisional labels remain provisional until confirmed by the user. Crops of the same physical plate are correlated, not independent plates.",
        "", "All engines run locally on this Mac with one CPU inference thread each, sequentially with alternating order. No camera, network, Gemini, or image-file loading time is included in OCR latency. Enhancement runs once per crop and is recorded separately in JSON. Engines retain their own decoding and internal filters. PP-OCRv5 uses its mobile detector at maximum side 736 and no orientation classifier for these upright crops. This includes text detection plus recognition, not registration-plate localization.",
        "The English specialist is evaluated only on samples declared English in the manifest. This is a configured language profile, not an automatic Thai/English router; its result must not be represented as multilingual accuracy.",
        "Exact match and CER use the first warm output for each crop; repeated reads measure latency, not additional independent accuracy samples. Spaces and punctuation are removed, Latin case is ignored, Unicode NFC is applied, and Thai marks are preserved. CER is edit distance divided by reference character count; lower is better. Confidence scores are not calibrated across engines.",
        "", "| Text group | Input | Engine | Exact matches | CER | Warm median | Warm p95 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in summaries:
        report.append(f"| {row['group']} | {row['variant']} | {row['engine']} | {row['exact_matches']}/{row['samples']} | {row['cer']:.1%} | {row['median_ms']:.1f} ms | {row['p95_ms']:.1f} ms |")
    report.extend(["", "## Model startup", "", "| Engine | Import/weights/session ready | First inference |", "|---|---:|---:|"])
    for engine, values in ready.items():
        report.append(f"| {engine} | {values['model_ready_ms']:.1f} ms | {values['first_inference_ms']:.1f} ms |")
    report.extend(["", "These are fresh reader instances in this benchmark process, not a guaranteed cold filesystem cache or total Python process startup. Downloads are excluded.", "", "## Predictions", "", "| Crop | Expected | Input | " + " | ".join(readers) + " |", "|---|---|---|" + "---|" * len(readers)])
    for sample in labels:
        for variant in ("raw", "enhanced"):
            pair = {r["engine"]: r for r in rows if r["id"] == sample["id"] and r["variant"] == variant}
            predictions = [str(pair[engine]["predicted"] or "∅") if engine in pair else "not applicable" for engine in readers]
            report.append(f"| {sample['id']} | {sample['expected']} | {variant} | " + " | ".join(predictions) + " |")
    command = ["python", "scripts/benchmark_ocr.py", "--labels", args.labels, "--images", args.images,
               "--repeats", str(args.repeats), "--output", args.output, "--report", args.report]
    if args.only_ids:
        command.extend(["--only-ids", args.only_ids])
    if args.paddle:
        command.append("--paddle")
    if args.english_specialist:
        command.append("--english-specialist")
    setup = ["python -m pip install -r requirements-ocr-compare.txt", "python scripts/setup_openthai.py"]
    if args.paddle or args.english_specialist:
        setup.append("python scripts/setup_paddleocr.py")
    report.extend(["", "## Scope and reproducibility", "", "This is a small, hand-selected set. Images of the same physical plate are correlated; repeated OCR calls add timing observations, not new accuracy samples. Provisional references need human confirmation. These results do not establish production accuracy. Test held-out entry/exit frames and additional physical plates before choosing a production default.", "", "OpenThai release: `v2.0.0`, commit `" + MODEL_REVISION + "`. The old model URL redirects to 2.1; the setup script pins the requested release and verifies file checksums.", "", "```bash", *setup, shlex.join(command), "```", "", "```json", json.dumps(metadata, ensure_ascii=False, indent=2), "```", ""])
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(report), encoding="utf-8")
    print(f"Saved {args.output} and {args.report}", flush=True)


if __name__ == "__main__":
    main()
