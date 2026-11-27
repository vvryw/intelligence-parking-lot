from __future__ import annotations

import asyncio
import logging
import os
from base64 import b64encode
from collections.abc import AsyncIterator
from pathlib import Path
from threading import Lock
from time import perf_counter

import cv2
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .image_source import ImageSourceError, decode_image, fetch_camera_image
from .pipeline import ParkingAIPipeline
from .schemas import parse_bbox
from .cameras import CAMERA_LABELS, CameraRole, camera_url
from .ocr_comparison import OCRComparison
from .ocr_reader import EasyOCRReader
from .plate_pipeline import crop_manual
from .parking_store import ParkingStore
from .parking_recognition import ParkingRecognition
from .parking_api import parking_router
from .supabase_parking import create_parking_store

app = FastAPI(title="Intelligence Parking Lot Day 1 AI", version="0.1.0")
pipeline = ParkingAIPipeline()
ocr_comparison = OCRComparison(EasyOCRReader(), paddle=pipeline.ocr_reader)
MAX_IMAGE_BYTES = 15 * 1024 * 1024
WEB_DIR = Path(__file__).resolve().parent / "web"
camera_locks = {role: Lock() for role in CAMERA_LABELS}
logger = logging.getLogger(__name__)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


def camera_address(camera: CameraRole = "entrance") -> str:
    try:
        return camera_url(camera)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


def read_camera(url: str, timeout_seconds: float = 10.0):
    # The ESP32 serves one HTTP request at a time. Release the lock before AI runs.
    role = "exit" if url == os.getenv("ESP32_EXIT_CAPTURE_URL", "").strip() else "entrance"
    with camera_locks[role]:
        return fetch_camera_image(url, timeout_seconds=timeout_seconds)


def camera_jpeg(url: str, *, timeout_seconds: float = 5.0, quality: int = 82) -> bytes:
    image, _ = read_camera(url, timeout_seconds=timeout_seconds)
    encoded, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not encoded:
        raise ImageSourceError("Could not encode camera preview")
    return data.tobytes()


async def preview_frames(url: str, first_frame: bytes, frame_started: float) -> AsyncIterator[bytes]:
    frame = first_frame
    while True:
        yield (
            f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode("ascii")
            + frame + b"\r\n"
        )
        # Cap fast connections at four requests per second without adding a
        # fixed delay when the camera already takes longer than the interval.
        await asyncio.sleep(max(0.0, 0.25 - (perf_counter() - frame_started)))
        frame_started = perf_counter()
        try:
            frame = await asyncio.to_thread(camera_jpeg, url)
        except ImageSourceError:
            # End this stream so the browser can reconnect if the camera returns.
            yield b"--frame--\r\n"
            return


@app.get("/", include_in_schema=False)
def camera_page() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/camera/stream")
async def camera_stream(camera: CameraRole = "entrance") -> StreamingResponse:
    url = camera_address(camera)
    started = perf_counter()
    try:
        first_frame = await asyncio.to_thread(camera_jpeg, url)
    except ImageSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return StreamingResponse(
        preview_frames(url, first_frame, started),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/camera/snapshot")
async def camera_snapshot(background_tasks: BackgroundTasks, camera: CameraRole = "entrance") -> Response:
    url = camera_address(camera)
    started = perf_counter()
    try:
        photo = await asyncio.to_thread(camera_jpeg, url, timeout_seconds=8.0, quality=95)
    except ImageSourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    elapsed_ms = (perf_counter() - started) * 1000
    background_tasks.add_task(warm_ocr)
    return Response(photo, media_type="image/jpeg", headers={
        "Cache-Control": "no-store", "X-Camera-Ms": f"{elapsed_ms:.1f}", "X-Camera-Role": camera,
    })


def warm_ocr() -> None:
    try:
        pipeline.ocr_reader.warm_up()
    except Exception as exc:
        logger.warning("PaddleOCR preload failed: %s", exc)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def decode_upload(image: UploadFile):
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds 15 MiB limit")
    try:
        return await asyncio.to_thread(decode_image, data)
    except ImageSourceError as exc:
        raise HTTPException(400, str(exc)) from exc


parking_store = create_parking_store()
app.include_router(parking_router(ParkingRecognition(pipeline, parking_store), decode_upload))
if getattr(parking_store, "passage_mode", False):
    from .passage_api import passage_router

    app.include_router(passage_router(parking_store))


@app.post("/classify")
async def classify(image: UploadFile = File(...), camera: CameraRole = Form("entrance")) -> dict:
    if camera == "exit":
        raise HTTPException(400, "Exit uses local OCR. Send the photo to /plate/read.")
    decoded = await decode_upload(image)
    result = await asyncio.to_thread(pipeline.vehicle_detector.detect, decoded)
    return {
        "camera": camera,
        "vehicle_type": result.vehicle_type,
        "confidence": result.confidence,
        "plate_bbox": result.plate_bbox,
        "needs_review": result.needs_review,
        "error": result.error,
        "model": result.model or pipeline.vehicle_detector.service.model,
        "processing_ms": round(result.processing_ms, 1),
    }


def read_selected_plate(image, bbox: str | None, camera: CameraRole = "entrance") -> dict:
    started = perf_counter()
    selected_box = parse_bbox(bbox)
    crop = pipeline.extract_plate(image, selected_box, exit_camera=camera == "exit")
    preprocess_ms = (perf_counter() - started) * 1000 - (crop.box_model_ms or 0)
    box_details = {"plate_box": crop.box_metadata()} if camera == "exit" else {}
    if crop.image is None:
        if selected_box is not None:
            raise ValueError(crop.error or "Draw a valid box around the license plate")
        return {
            **box_details,
            "license_plate": None,
            "ocr_engine": pipeline.ocr_reader.engine,
            "bbox": None,
            "confidence": None,
            "status": "needs_review",
            "error": "No clear plate found. Draw a box around the registration characters.",
            "enhanced_plate": None,
            "preprocess_ms": round(preprocess_ms, 1),
            "ocr_ms": None,
        }
    result = pipeline.read_cropped_plate(image, crop)
    success, jpeg = cv2.imencode(".jpg", crop.image)
    if not success:
        raise ValueError("Could not encode the enhanced plate crop")
    return {
        **box_details,
        "license_plate": result.text,
        "ocr_engine": result.engine,
        "bbox": crop.bbox,
        "confidence": result.confidence,
        "status": result.status,
        "error": result.error,
        "enhanced_plate": f"data:image/jpeg;base64,{b64encode(jpeg).decode('ascii')}",
        "preprocess_ms": round(preprocess_ms, 1),
        "ocr_ms": round(result.processing_ms, 1),
    }


@app.post("/plate/read")
async def plate_read(
    image: UploadFile = File(...),
    plate_bbox: str | None = Form(None, min_length=1),
    camera: CameraRole = Form("entrance"),
) -> dict:
    if plate_bbox is None and camera == "entrance":
        raise HTTPException(422, "Select a plate box before reading the entrance photo.")
    decoded = await decode_upload(image)
    try:
        result = await asyncio.to_thread(read_selected_plate, decoded, plate_bbox, camera)
        return {"camera": camera, **result}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def compare_selected_plate(image, bbox: str, expected: str | None = None, enhance: bool = True, include_paddle: bool = False) -> dict:
    started = perf_counter()
    parsed = parse_bbox(bbox)
    if parsed is None:
        raise ValueError("Draw a valid box around the license plate")
    crop = pipeline.plate_pipeline.extract(image, parsed) if enhance else crop_manual(image, parsed)
    preprocess_ms = (perf_counter() - started) * 1000
    if crop.image is None:
        raise ValueError(crop.error or "Draw a valid box around the license plate")
    success, jpeg = cv2.imencode(".jpg", crop.image)
    if not success:
        raise ValueError("Could not encode the enhanced plate crop")
    kwargs = {"include_paddle": True} if include_paddle else {}
    return {
        "bbox": crop.bbox,
        "enhanced_plate": f"data:image/jpeg;base64,{b64encode(jpeg).decode('ascii')}",
        "preprocess_ms": round(preprocess_ms, 1),
        "results": ocr_comparison.compare(crop.image, crop.bbox, expected, **kwargs),
        "input_variant": "enhanced" if enhance else "raw",
        "note": "Same crop; sequential local reads. OCR time excludes model loading and image preparation. Confidence values are not comparable between engines.",
    }


@app.post("/plate/compare")
async def plate_compare(
    image: UploadFile = File(...), plate_bbox: str = Form(..., min_length=1),
    camera: CameraRole = Form("entrance"), expected: str | None = Form(None, max_length=80),
    enhance: bool = Form(True),
    include_paddle: bool = Form(False),
) -> dict:
    decoded = await decode_upload(image)
    try:
        result = await asyncio.to_thread(compare_selected_plate, decoded, plate_bbox, expected, enhance, include_paddle)
        return {"camera": camera, **result}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/infer")
async def infer(
    image: UploadFile = File(...),
    plate_bbox: str | None = Form(default=None),
    camera: CameraRole = Form("entrance"),
) -> dict:
    decoded = await decode_upload(image)
    try:
        bbox = parse_bbox(plate_bbox)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await asyncio.to_thread(
        pipeline.run_dict,
        decoded,
        image.filename or "upload.jpg",
        bbox,
        classify_vehicle=camera == "entrance",
    )


@app.post("/capture")
async def capture(
    plate_bbox: str | None = Form(default=None),
    camera: CameraRole = Form("entrance"),
) -> dict:
    camera_url = camera_address(camera)
    try:
        bbox = parse_bbox(plate_bbox)
        image, image_id = await asyncio.to_thread(read_camera, camera_url)
    except (ImageSourceError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    result = await asyncio.to_thread(
        pipeline.run_dict, image, image_id, bbox, classify_vehicle=camera == "entrance"
    )
    return {"camera": camera, **result}

