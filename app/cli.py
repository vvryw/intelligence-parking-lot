from __future__ import annotations

import argparse
import json
import os
import sys

from .image_source import ImageSourceError, fetch_camera_image, load_local_image
from .pipeline import ParkingAIPipeline
from .schemas import parse_bbox


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.image:
            image, image_id = load_local_image(args.image)
        else:
            camera_url = args.camera_url or os.getenv("ESP32_CAPTURE_URL", "")
            if not camera_url:
                raise ImageSourceError("Provide --camera-url URL or set ESP32_CAPTURE_URL")
            image, image_id = fetch_camera_image(camera_url)
        result = ParkingAIPipeline().run(
            image,
            image_id,
            parse_bbox(args.plate_bbox),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except (ImageSourceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

