from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

BBox = tuple[int, int, int, int]
VehicleType = Literal["car", "sportcar", "supercar", "unknown"]
VEHICLE_TYPES = get_args(VehicleType)
ReviewStatus = Literal["success", "needs_review", "failed"]


def normalize_vehicle_type(value: str) -> str:
    label = value.strip().lower()
    return "sportcar" if label in {"sports car", "sports_car", "sportscar"} else label


def parse_bbox(value: str | None) -> BBox | None:
    if not value:
        return None
    try:
        parts = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise ValueError("plate_bbox must contain four integers: x1,y1,x2,y2") from exc
    if len(parts) != 4:
        raise ValueError("plate_bbox must contain four integers: x1,y1,x2,y2")
    return parts  # type: ignore[return-value]


@dataclass(slots=True)
class VehicleResult:
    vehicle_type: VehicleType = "unknown"
    confidence: float | None = None
    bbox: BBox | None = None
    processing_ms: float = 0.0
    needs_review: bool = True
    error: str | None = None
    plate_bbox: BBox | None = None
    model: str | None = None


@dataclass(slots=True)
class OCRResult:
    text: str | None = None
    confidence: float | None = None
    bbox: BBox | None = None
    engine: str = "easyocr"
    processing_ms: float = 0.0
    status: ReviewStatus = "failed"
    error: str | None = None


@dataclass(slots=True)
class PipelineResult:
    image_id: str
    vehicle: VehicleResult | None
    ocr: OCRResult
    total_ms: float
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        status: ReviewStatus
        vehicle = self.vehicle
        needs_review = bool(vehicle and vehicle.needs_review) or self.ocr.status != "success"
        if vehicle is None:
            status = self.ocr.status
        elif vehicle.error and self.ocr.error:
            status = "failed"
        elif needs_review:
            status = "needs_review"
        else:
            status = "success"

        result: dict[str, Any] = {
            "image_id": self.image_id,
            "vehicle_type": vehicle.vehicle_type if vehicle else None,
            "vehicle_confidence": vehicle.confidence if vehicle else None,
            "vehicle_bbox": list(vehicle.bbox) if vehicle and vehicle.bbox else None,
            "license_plate": self.ocr.text,
            "ocr_confidence": self.ocr.confidence,
            "plate_bbox": list(self.ocr.bbox) if self.ocr.bbox else None,
            "ocr_engine": self.ocr.engine,
            "status": status,
            "needs_review": needs_review,
            "processing_ms": {
                "vehicle": round(vehicle.processing_ms, 3) if vehicle else None,
                "plate_and_ocr": round(self.ocr.processing_ms, 3),
                "total": round(self.total_ms, 3),
            },
            "errors": self.errors,
        }
        return result

