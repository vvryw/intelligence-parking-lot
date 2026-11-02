from __future__ import annotations

from time import perf_counter
from typing import Any

import numpy as np

from .ocr_reader import GeminiOCRReader
from .plate_pipeline import PlatePipeline
from .schemas import BBox, OCRResult, PipelineResult
from .vehicle_detector import GeminiVehicleDetector


class ParkingAIPipeline:
    def __init__(
        self,
        vehicle_detector: GeminiVehicleDetector | None = None,
        plate_pipeline: PlatePipeline | None = None,
        ocr_reader: GeminiOCRReader | None = None,
    ) -> None:
        self.vehicle_detector = vehicle_detector or GeminiVehicleDetector()
        self.plate_pipeline = plate_pipeline or PlatePipeline()
        self.ocr_reader = ocr_reader or GeminiOCRReader()

    def run(
        self,
        image: np.ndarray,
        image_id: str,
        plate_bbox: BBox | None = None,
    ) -> PipelineResult:
        started = perf_counter()
        vehicle = self.vehicle_detector.detect(image)

        plate_started = perf_counter()
        plate = self.plate_pipeline.extract(image, plate_bbox)
        if plate.image is None:
            ocr = OCRResult(bbox=plate.bbox, error=plate.error, status="needs_review")
        else:
            ocr = self.ocr_reader.read(plate.image, plate.bbox)
        ocr.processing_ms = (perf_counter() - plate_started) * 1000

        errors = list(
            dict.fromkeys(message for message in (vehicle.error, plate.error, ocr.error) if message)
        )
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
    ) -> dict[str, Any]:
        """Run inference and return the JSON-ready API/CLI representation."""
        return self.run(
            image, image_id, plate_bbox
        ).to_dict()

