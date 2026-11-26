from __future__ import annotations

from time import perf_counter
from typing import Any

import numpy as np

from .ocr_reader import EasyOCRReader
from .plate_pipeline import PlatePipeline
from .schemas import BBox, OCRResult, PipelineResult
from .vehicle_detector import GeminiVehicleDetector


class ParkingAIPipeline:
    def __init__(
        self,
        vehicle_detector: GeminiVehicleDetector | None = None,
        plate_pipeline: PlatePipeline | None = None,
        ocr_reader: EasyOCRReader | None = None,
    ) -> None:
        self.vehicle_detector = vehicle_detector or GeminiVehicleDetector()
        self.plate_pipeline = plate_pipeline or PlatePipeline()
        self.ocr_reader = ocr_reader or EasyOCRReader()

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
        plate = self.plate_pipeline.extract(image, plate_bbox)
        if plate.image is None:
            ocr = OCRResult(bbox=plate.bbox, error=plate.error, status="needs_review")
        else:
            ocr = self.ocr_reader.read(plate.image, plate.bbox)
        ocr.processing_ms = (perf_counter() - plate_started) * 1000

        vehicle_error = vehicle.error if vehicle is not None else None
        errors = list(dict.fromkeys(message for message in (vehicle_error, plate.error, ocr.error) if message))
        return PipelineResult(
            image_id=image_id,
            vehicle=vehicle,
            ocr=ocr,
            total_ms=(perf_counter() - started) * 1000,
            errors=errors,
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

