from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.image_source import load_local_image
from app.ocr_reader import normalize_plate_text
from app.pipeline import ParkingAIPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate labeled Day 1 still images")
    parser.add_argument("--labels", default="data/labels.csv")
    parser.add_argument("--images", default="data/samples")
    parser.add_argument("--output", default="reports/results.csv")
    parser.add_argument("--report", default="reports/day1_results.md")
    args = parser.parse_args()

    labels_path, images_dir, output_path, report_path = map(
        Path, (args.labels, args.images, args.output, args.report)
    )
    pipeline = ParkingAIPipeline()
    rows: list[dict[str, object]] = []
    with labels_path.open(encoding="utf-8-sig", newline="") as handle:
        for truth in csv.DictReader(handle):
            image, image_id = load_local_image(images_dir / truth["filename"])
            raw_bbox = (truth.get("plate_bbox") or "").strip()
            bbox = tuple(int(value) for value in raw_bbox.split(",")) if raw_bbox else None
            if bbox is not None and len(bbox) != 4:
                raise ValueError(f"Invalid plate_bbox for {image_id}: {raw_bbox}")
            result = pipeline.run(image, image_id, bbox).to_dict()
            expected_plate = normalize_plate_text(truth.get("plate", ""))
            rows.append(
                {
                    **truth,
                    "predicted_vehicle": result["vehicle_type"],
                    "vehicle_correct": result["vehicle_type"] == truth["vehicle_type"],
                    "predicted_plate": result["license_plate"] or "",
                    "plate_exact_match": result["license_plate"] == expected_plate,
                    "needs_review": result["needs_review"],
                    "total_ms": result["processing_ms"]["total"],
                    "processing_fps": round(1000 / result["processing_ms"]["total"], 3)
                    if result["processing_ms"]["total"]
                    else "",
                    "errors": " | ".join(result["errors"]),
                }
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    vehicle_labeled = [
        row for row in rows if row["vehicle_type"] in {"car", "sportcar", "supercar"}
    ]
    plate_labeled = [row for row in rows if normalize_plate_text(str(row["plate"]))]
    vehicle_correct = sum(row["vehicle_correct"] is True for row in vehicle_labeled)
    plate_correct = sum(row["plate_exact_match"] is True for row in plate_labeled)
    reviews = sum(row["needs_review"] is True for row in rows)
    unknowns = sum(row["predicted_vehicle"] == "unknown" for row in rows)
    latencies = [float(row["total_ms"]) for row in rows]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary = [
        "# Day 1 measured evaluation\n",
        "> Generated from the local labeled images. Processing FPS excludes camera/network time.\n",
        f"- Images: {len(rows)}",
    ]
    summary.append(
        f"- Vehicle accuracy: {vehicle_correct}/{len(vehicle_labeled)} "
        f"({vehicle_correct / len(vehicle_labeled):.1%})"
        if vehicle_labeled
        else "- Vehicle accuracy: no labeled images"
    )
    summary.append(f"- Vehicle unknown outputs: {unknowns}/{len(rows)}" if rows else "- Vehicle unknown outputs: no images")
    summary.append(
        f"- Plate exact-match accuracy: {plate_correct}/{len(plate_labeled)} "
        f"({plate_correct / len(plate_labeled):.1%})"
        if plate_labeled
        else "- Plate exact-match accuracy: no readable plates labeled"
    )
    summary.append(
        f"- Review/failure rate: {reviews}/{len(rows)} ({reviews / len(rows):.1%})"
        if rows
        else "- Review/failure rate: no images"
    )
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    if mean_latency > 0:
        summary.extend(
            [
                f"- Mean end-to-end processing latency: {mean_latency:.1f} ms",
                f"- Mean processing throughput: {1000 / mean_latency:.2f} FPS",
            ]
        )
    else:
        summary.append("- Latency/throughput: no measurements")
    report_path.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} evaluated images to {output_path}")
    print(f"Wrote summary to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

