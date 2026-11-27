from __future__ import annotations

import os
from time import perf_counter
from typing import Any

import numpy as np

from .gemini_service import GeminiServiceError
from .ocr_reader import OCRReader
from .paddleocr_reader import PaddleOCRReader
from .plate_pipeline import PlateCrop, PlatePipeline
from .schemas import BBox, OCRResult, PipelineResult
from .vehicle_detector import GeminiVehicleDetector


class ParkingAIPipeline:
    def __init__(
        self,
        vehicle_detector: GeminiVehicleDetector | None = None,
        plate_pipeline: PlatePipeline | None = None,
        ocr_reader: OCRReader | None = None,
    ) -> None:
        self.vehicle_detector = vehicle_detector or GeminiVehicleDetector()
        self.plate_pipeline = plate_pipeline or PlatePipeline()
        self.ocr_reader = ocr_reader or PaddleOCRReader()

    def extract_plate(
        self, image: np.ndarray, plate_bbox: BBox | None, *, exit_camera: bool
    ) -> PlateCrop:
        enabled = os.getenv("GEMINI_EXIT_PLATE_BOX", "true").strip().lower() in {"true", "1"}
        if plate_bbox is not None or not exit_camera or not enabled:
            return self.plate_pipeline.extract(image, plate_bbox)

        started = perf_counter()
        try:
            candidate, model = self.vehicle_detector.locate_plate(image)
        except GeminiServiceError:
            candidate, model = None, None
        elapsed = (perf_counter() - started) * 1000

        crop = self.plate_pipeline.extract(image, candidate)
        crop.box_model_ms = elapsed
        if candidate is not None:
            crop.method = "gemini"
            crop.box_model = model
        else:
            crop.method = "heuristic_fallback"
        return crop

    def read_cropped_plate(self, image: np.ndarray, crop: PlateCrop) -> OCRResult:
        if crop.image is None:
            return OCRResult(
                bbox=crop.bbox, error=crop.error, status="needs_review",
                engine=getattr(self.ocr_reader, "engine", "paddleocr-v5-th"),
            )

        enhanced = self.ocr_reader.read(crop.image, crop.bbox)
        if crop.method != "gemini" or crop.bbox is None:
            return enhanced

        # Contrast enhancement can erase a faint character. Compare the same
        # AI-selected pixels before and after enhancement to catch that.
        x1, y1, x2, y2 = crop.bbox
        raw_image = image[y1:y2, x1:x2].copy()
        raw = self.ocr_reader.read(raw_image, crop.bbox)
        total_ms = enhanced.processing_ms + raw.processing_ms
        if raw.status == "success" and raw.text:
            if enhanced.status != "success" or not enhanced.text:
                raw.status = "needs_review"
                raw.error = "Only the raw crop produced a confident reading. Redraw the box or retake the photo."
                crop.image = raw_image
                raw.processing_ms = total_ms
                return raw
            if enhanced.text != raw.text and enhanced.text in raw.text:
                crop.image = raw_image
                raw.processing_ms = total_ms
                return raw
            if raw.text not in enhanced.text:
                enhanced.status = "needs_review"
                enhanced.error = "Raw and enhanced crops produced different plate text. Redraw the box or retake the photo."

        enhanced.processing_ms = total_ms
        return enhanced

    def run(
        self,
        image: np.ndarray,
        image_id: str,
        plate_bbox: BBox | None = None,
        *,
        classify_vehicle: bool = True,
    ) -> PipelineResult:
        started = perf_counter()
        vehicle = self.vehicle_detector.detect(image) if classify_vehicle else None

        plate_started = perf_counter()
        if plate_bbox is None and vehicle is not None:
            plate_bbox = vehicle.plate_bbox
        plate = self.extract_plate(image, plate_bbox, exit_camera=not classify_vehicle)
        ocr = self.read_cropped_plate(image, plate)
        ocr.processing_ms = (perf_counter() - plate_started) * 1000

        vehicle_error = vehicle.error if vehicle is not None else None
        errors = list(dict.fromkeys(message for message in (vehicle_error, plate.error, ocr.error) if message))
        return PipelineResult(
            image_id=image_id,
            vehicle=vehicle,
            ocr=ocr,
            total_ms=(perf_counter() - started) * 1000,
            errors=errors,
            plate_box=plate.box_metadata() if not classify_vehicle else None,
        )

    def run_dict(
        self,
        image: np.ndarray,
        image_id: str,
        plate_bbox: BBox | None = None,
        *,
        classify_vehicle: bool = True,
    ) -> dict[str, Any]:
        """Run inference and return the JSON-ready API/CLI representation."""
        return self.run(
            image, image_id, plate_bbox, classify_vehicle=classify_vehicle
        ).to_dict()

