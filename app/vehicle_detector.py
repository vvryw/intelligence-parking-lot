from __future__ import annotations

import os
from time import perf_counter

import numpy as np

from .gemini_service import GeminiJSONService
from .schemas import BBox, VEHICLE_TYPES, VehicleResult, normalize_vehicle_type

NORMALIZED_BOX_SCHEMA = {
    "anyOf": [
        {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 1000},
            "minItems": 4,
            "maxItems": 4,
        },
        {"type": "null"},
    ]
}

VEHICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "vehicle_type": {"type": "string", "enum": list(VEHICLE_TYPES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "bbox_1000": NORMALIZED_BOX_SCHEMA,
        "plate_bbox_1000": NORMALIZED_BOX_SCHEMA,
    },
    "required": ["vehicle_type", "confidence", "bbox_1000", "plate_bbox_1000"],
}

PLATE_BOX_SCHEMA = {
    "type": "object",
    "properties": {"plate_bbox_1000": NORMALIZED_BOX_SCHEMA},
    "required": ["plate_bbox_1000"],
}

PLATE_BOX_PROMPT = """Locate the registration characters on the license plate of the single dominant
vehicle in this exit camera photo. The vehicle may be a toy car with a paper plate.
Include every registration character and a small margin on all sides. Exclude any
separate province line, body logos, stickers and background text. If a plate is
missing or you cannot identify a single plate, return null. Return only plate_bbox_1000
as [y_min, x_min, y_max, x_max] in 0–1000 coordinates over the original image.
Do not classify the vehicle or transcribe the plate."""

VEHICLE_PROMPT = """Classify the single dominant vehicle from its visible design.
If the subject is a scale model, classify the vehicle it represents.
- car: ordinary passenger vehicle such as a sedan, hatchback, wagon, SUV, or family coupe.
- sportcar: purpose-built sports car such as a Mazda MX-5, Toyota GR86, Ford Mustang, or Porsche 911 Carrera.
  Look for a low, compact performance coupe or roadster. Sport trim on a family car is still car.
- supercar: exotic high-performance car such as a Lamborghini Huracán, Ferrari 488, or McLaren 720S.
  Look for distinctive exotic cues such as a very low wide body, cab-forward proportions, or large side intakes.
- unknown: the target is unclear, there are several competing vehicles, the type is unsupported,
  or the angle and blur hide the features needed to distinguish the classes.
Ignore plate text and people when classifying. Give confidence from 0 to 1. Put a box around the vehicle
in bbox_1000. In plate_bbox_1000, locate the registration characters on that vehicle's license plate.
Draw a tight box that includes all registration characters with a little margin and excludes the province
line when it is separate. Do not transcribe the text. Use null for an unclear or missing box.
Return both boxes as [y_min, x_min, y_max, x_max] on a 0–1000 scale over the original image."""


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


def _usable_plate_bbox(raw: object, image: np.ndarray, vehicle_bbox: BBox | None) -> BBox | None:
    plate = _bbox_from_normalized(raw, image)
    if plate is None:
        return None
    x1, y1, x2, y2 = plate
    if x2 - x1 < 8 or y2 - y1 < 6 or vehicle_bbox == plate:
        return None
    if vehicle_bbox:
        left, top, right, bottom = vehicle_bbox
        if not (left <= (x1 + x2) / 2 <= right and top <= (y1 + y2) / 2 <= bottom):
            return None
    height, width = image.shape[:2]
    if (x2 - x1) * (y2 - y1) > width * height / 3:
        return None
    return plate


class GeminiVehicleDetector:
    """Gemini classification; the service confidence is useful for review, not calibrated."""

    def __init__(self, service: GeminiJSONService | None = None, min_confidence: float | None = None) -> None:
        self.service = service or GeminiJSONService()
        self.min_confidence = min_confidence if min_confidence is not None else float(
            os.getenv("GEMINI_MIN_VEHICLE_CONFIDENCE", "0.60")
        )

    def locate_plate(self, image: np.ndarray) -> tuple[BBox | None, str]:
        payload, model = self.service.analyze_with_model(image, PLATE_BOX_PROMPT, PLATE_BOX_SCHEMA)
        raw = payload.get("plate_bbox_1000")
        if not isinstance(raw, list) or len(raw) != 4 or any(type(value) is not int for value in raw):
            return None, model
        y1, x1, y2, x2 = raw
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
            return None, model
        return _usable_plate_bbox(raw, image, None), model

    def detect(self, image: np.ndarray) -> VehicleResult:
        started = perf_counter()
        try:
            payload, model = self.service.analyze_with_model(image, VEHICLE_PROMPT, VEHICLE_SCHEMA)
            vehicle_type = normalize_vehicle_type(str(payload.get("vehicle_type", "unknown")))
            if vehicle_type not in VEHICLE_TYPES:
                raise ValueError("Gemini returned an unsupported vehicle type")
            confidence = float(payload["confidence"])
            if not 0 <= confidence <= 1:
                raise ValueError("Gemini confidence is outside 0..1")
            bbox = _bbox_from_normalized(payload.get("bbox_1000"), image)
            plate_bbox = _usable_plate_bbox(payload.get("plate_bbox_1000"), image, bbox)
            needs_review = vehicle_type == "unknown" or confidence < self.min_confidence
            if needs_review:
                return VehicleResult(
                    confidence=confidence,
                    bbox=bbox,
                    processing_ms=(perf_counter() - started) * 1000,
                    needs_review=True,
                    error="Gemini classification is unknown or below the review threshold",
                    plate_bbox=plate_bbox,
                    model=model,
                )
            return VehicleResult(
                vehicle_type=vehicle_type,  # type: ignore[arg-type]
                confidence=confidence,
                bbox=bbox,
                processing_ms=(perf_counter() - started) * 1000,
                needs_review=False,
                plate_bbox=plate_bbox,
                model=model,
            )
        except Exception as exc:
            return VehicleResult(
                processing_ms=(perf_counter() - started) * 1000,
                error=f"Gemini vehicle analysis failed: {exc}",
            )
