from __future__ import annotations

import os
from time import perf_counter

import numpy as np

from .gemini_service import GeminiJSONService
from .schemas import BBox, VehicleResult

VEHICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "vehicle_type": {"type": "string", "enum": ["car", "sportcar", "supercar", "unknown"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "bbox_1000": {
            "anyOf": [
                {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                    "minItems": 4,
                    "maxItems": 4,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["vehicle_type", "confidence", "bbox_1000"],
}

VEHICLE_PROMPT = """Analyze this parking-lot image. Classify the single dominant vehicle only as:
- car: ordinary passenger car,
- sportcar: performance-oriented sports car that is not an exotic supercar,
- supercar: exotic/high-performance supercar,
- unknown: no clear target vehicle, multiple competing vehicles, or insufficient visual evidence.
Do not guess. Give your own confidence from 0 to 1. If one vehicle is clearly identifiable,
return its bounding box as [y_min, x_min, y_max, x_max], normalized 0-1000; otherwise null."""


def _bbox_from_normalized(raw: object, image: np.ndarray) -> BBox | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        y1, x1, y2, x2 = (int(value) for value in raw)
    except (TypeError, ValueError):
        return None
    height, width = image.shape[:2]
    x1, x2 = sorted((max(0, min(width, round(x1 * width / 1000))), max(0, min(width, round(x2 * width / 1000)))))
    y1, y2 = sorted((max(0, min(height, round(y1 * height / 1000))), max(0, min(height, round(y2 * height / 1000)))))
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


class GeminiVehicleDetector:
    """Gemini classification; the service confidence is useful for review, not calibrated."""

    allowed_types = {"car", "sportcar", "supercar", "unknown"}

    def __init__(self, service: GeminiJSONService | None = None, min_confidence: float | None = None) -> None:
        self.service = service or GeminiJSONService()
        self.min_confidence = min_confidence if min_confidence is not None else float(
            os.getenv("GEMINI_MIN_VEHICLE_CONFIDENCE", "0.60")
        )

    def detect(self, image: np.ndarray) -> VehicleResult:
        started = perf_counter()
        try:
            payload = self.service.analyze(image, VEHICLE_PROMPT, VEHICLE_SCHEMA)
            vehicle_type = str(payload.get("vehicle_type", "unknown")).lower()
            if vehicle_type not in self.allowed_types:
                raise ValueError("Gemini returned an unsupported vehicle type")
            confidence = float(payload["confidence"])
            if not 0 <= confidence <= 1:
                raise ValueError("Gemini confidence is outside 0..1")
            bbox = _bbox_from_normalized(payload.get("bbox_1000"), image)
            needs_review = vehicle_type == "unknown" or confidence < self.min_confidence
            if needs_review:
                return VehicleResult(
                    confidence=confidence,
                    bbox=bbox,
                    processing_ms=(perf_counter() - started) * 1000,
                    needs_review=True,
                    error="Gemini classification is unknown or below the review threshold",
                )
            return VehicleResult(
                vehicle_type=vehicle_type,  # type: ignore[arg-type]
                confidence=confidence,
                bbox=bbox,
                processing_ms=(perf_counter() - started) * 1000,
                needs_review=False,
            )
        except Exception as exc:
            return VehicleResult(
                processing_ms=(perf_counter() - started) * 1000,
                error=f"Gemini vehicle analysis failed: {exc}",
            )
