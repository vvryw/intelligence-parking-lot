"""Test existing plate localizers against a manual registration-row reference."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2

from app.plate_pipeline import crop_manual, locate_plate_heuristic
from app.schemas import parse_bbox
from app.vehicle_detector import GeminiVehicleDetector


def box_iou(left, right) -> float:
    if left is None:
        return 0.0
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    overlap = max(0, x2 - x1) * max(0, y2 - y1)
    area = lambda b: (b[2] - b[0]) * (b[3] - b[1])
    return overlap / (area(left) + area(right) - overlap)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--reference", required=True, help="Manual registration row: x1,y1,x2,y2")
    parser.add_argument("--output", default="reports/plate_bbox_test.json")
    parser.add_argument("--annotated", default="data/samples/ocr_benchmark/camera_bbox_test.jpg")
    parser.add_argument("--gemini", action="store_true", help="Also make one Gemini classification/localization request")
    args = parser.parse_args()
    image = cv2.imread(args.image)
    if image is None:
        raise ValueError("Cannot read image")
    reference = parse_bbox(args.reference)
    if reference is None or crop_manual(image, reference).bbox != reference:
        raise ValueError("Reference must be a valid box inside the image")
    started = perf_counter()
    heuristic = locate_plate_heuristic(image)
    rows = [
        {"method": "manual-reference", "bbox": reference, "ms": 0, "iou": 1},
        {"method": "opencv-heuristic", "bbox": heuristic.bbox, "ms": (perf_counter() - started) * 1000,
         "iou": box_iou(heuristic.bbox, reference), "error": heuristic.error},
    ]
    print("OpenCV heuristic:", rows[-1], flush=True)
    if args.gemini:
        detector = GeminiVehicleDetector()
        result = detector.detect(image)
        rows.append({"method": "gemini", "bbox": result.plate_bbox, "ms": result.processing_ms,
                     "iou": box_iou(result.plate_bbox, reference), "model": detector.service.model,
                     "error": result.error})
        print("Gemini:", rows[-1], flush=True)
    colors = [(80, 240, 100), (0, 170, 255), (255, 160, 80)]
    for row, color in zip(rows, colors):
        if row["bbox"]:
            x1, y1, x2, y2 = row["bbox"]
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            cv2.putText(image, row["method"], (x1, max(15, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    target = Path(args.annotated)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), image):
        raise RuntimeError("Cannot save annotated image")


if __name__ == "__main__":
    main()
