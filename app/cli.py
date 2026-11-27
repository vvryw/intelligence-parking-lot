from __future__ import annotations

import argparse
import json
import sys

from .image_source import ImageSourceError, fetch_camera_image, load_local_image, save_image
from .pipeline import ParkingAIPipeline
from .schemas import parse_bbox
from .cameras import CAMERA_LABELS, camera_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Day 1 parking AI on one still image")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="Local JPEG/PNG path")
    source.add_argument(
        "--camera-url",
        nargs="?",
        const="",
        help="ESP32 capture URL; defaults to ESP32_CAPTURE_URL",
    )
    parser.add_argument("--plate-bbox", help="Manual plate crop in original-image x1,y1,x2,y2")
    parser.add_argument("--camera", choices=CAMERA_LABELS, default="entrance", help="Gemini finds the plate box for either camera and classifies at entrance. OCR runs locally.")
    parser.add_argument(
        "--capture-only",
        metavar="OUTPUT",
        help="Save a camera JPEG/PNG and exit without Gemini or OCR (requires --camera-url)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.capture_only and (args.image or args.plate_bbox):
        parser.error("--capture-only requires --camera-url and cannot be combined with --plate-bbox")
    try:
        if args.image:
            image, image_id = load_local_image(args.image)
        else:
            source_url = args.camera_url or camera_url(args.camera)
            if not source_url:
                raise ImageSourceError("Provide --camera-url URL or set ESP32_CAPTURE_URL")
            image, image_id = fetch_camera_image(source_url)
        if args.capture_only:
            destination = save_image(image, args.capture_only)
            print(json.dumps({
                "status": "captured",
                "path": str(destination),
                "width": image.shape[1],
                "height": image.shape[0],
            }, indent=2))
            return 0
        result = ParkingAIPipeline().run(
            image,
            image_id,
            parse_bbox(args.plate_bbox),
            classify_vehicle=args.camera == "entrance",
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except (ImageSourceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

