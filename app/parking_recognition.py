from __future__ import annotations

import hashlib
from base64 import b64encode
from dataclasses import asdict
from time import perf_counter

import cv2

from .parking_store import ParkingStore, canonical_plate, space_kind
from .pipeline import ParkingAIPipeline
from .schemas import PipelineResult, VehicleResult


def jpeg_bytes(image) -> bytes:
    success, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not success:
        raise ValueError("Could not archive the camera image")
    return data.tobytes()


class ParkingRecognition:
    def __init__(self, pipeline: ParkingAIPipeline, store: ParkingStore):
        self.pipeline, self.store = pipeline, store

    def analyze(self, image, camera: str, bbox=None, previous_id: str | None = None) -> dict:
        started = perf_counter()
        fingerprint = hashlib.sha256(str(image.shape).encode() + image.tobytes()).hexdigest()
        vehicle = None
        if previous_id:
            previous = self.store.observation(previous_id)
            if previous["fingerprint"] != fingerprint or previous["camera"] != camera:
                raise ValueError("The previous analysis belongs to a different image or camera")
            cached = previous["recognition"].get("vehicle")
            if camera == "entrance" and cached:
                vehicle = VehicleResult(**cached)
        if camera == "entrance" and vehicle is None:
            vehicle = self.pipeline.vehicle_detector.detect(image)
        if bbox is None and vehicle:
            bbox = vehicle.plate_bbox
        crop_started = perf_counter()
        crop = self.pipeline.extract_plate(image, bbox, exit_camera=camera == "exit")
        preprocess_ms = (perf_counter() - crop_started) * 1000 - (crop.box_model_ms or 0)
        ocr = self.pipeline.read_cropped_plate(image, crop)
        crop_jpeg = jpeg_bytes(crop.image) if crop.image is not None else None
        result = PipelineResult(
            image_id=f"{camera}_capture", vehicle=vehicle, ocr=ocr,
            total_ms=(perf_counter() - started) * 1000,
            errors=list(dict.fromkeys(error for error in (vehicle.error if vehicle else None, crop.error, ocr.error) if error)),
            plate_box=crop.box_metadata() if camera == "exit" else None,
        ).to_dict()
        # This record is created on the server. The commit endpoint never trusts
        # a browser-provided plate, classification, confidence or approval flag.
        eligible = (
            ocr.status == "success" and canonical_plate(ocr.text) is not None
            and ocr.confidence is not None and ocr.confidence >= self.pipeline.ocr_reader.min_confidence
            and (camera == "exit" or bool(vehicle and not vehicle.needs_review and not vehicle.error and space_kind(vehicle.vehicle_type)))
        )
        recognition = {
            **result, "eligible": eligible, "vehicle": asdict(vehicle) if vehicle else None,
            "preprocess_ms": round(preprocess_ms, 1), "ocr_ms": round(ocr.processing_ms, 1),
            "model": vehicle.model if vehicle else None,
        }
        observation = self.store.save_observation(camera, fingerprint, jpeg_bytes(image), crop_jpeg, recognition)
        return {
            **recognition, "observation_id": observation["id"],
            "photo_url": observation["photo_url"], "crop_url": observation["crop_url"],
            "enhanced_plate": f"data:image/jpeg;base64,{b64encode(crop_jpeg).decode('ascii')}" if crop_jpeg else None,
        }
