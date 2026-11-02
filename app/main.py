from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from .image_source import ImageSourceError, decode_image, fetch_camera_image
from .pipeline import ParkingAIPipeline
from .schemas import parse_bbox

app = FastAPI(title="Intelligence Parking Lot Day 1 AI", version="0.1.0")
pipeline = ParkingAIPipeline()
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/infer")
async def infer(
    image: UploadFile = File(...),
    plate_bbox: str | None = Form(default=None),
) -> dict:
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds 15 MiB limit")
    try:
        decoded = decode_image(data)
        bbox = parse_bbox(plate_bbox)
    except (ImageSourceError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return await asyncio.to_thread(
        pipeline.run_dict,
        decoded,
        image.filename or "upload.jpg",
        bbox,
    )


@app.post("/capture")
async def capture(
    plate_bbox: str | None = Form(default=None),
) -> dict:
    camera_url = os.getenv("ESP32_CAPTURE_URL", "").strip()
    if not camera_url:
        raise HTTPException(503, "ESP32_CAPTURE_URL is not configured")
    try:
        image, image_id = await asyncio.to_thread(fetch_camera_image, camera_url)
        bbox = parse_bbox(plate_bbox)
    except (ImageSourceError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    return await asyncio.to_thread(
        pipeline.run_dict, image, image_id, bbox
    )

