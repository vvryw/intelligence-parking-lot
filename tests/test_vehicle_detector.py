import numpy as np

from app.gemini_service import GeminiJSONService
from app.vehicle_detector import GeminiVehicleDetector


IMAGE = np.zeros((100, 200, 3), dtype=np.uint8)


def service_with(payload):
    return GeminiJSONService(generate=lambda image, prompt, schema: payload)


def test_gemini_car_result_and_normalized_bbox():
    detector = GeminiVehicleDetector(
        service_with({
            "vehicle_type": "car", "confidence": 0.9,
            "bbox_1000": [100, 200, 800, 900], "plate_bbox_1000": [550, 400, 650, 700],
        })
    )
    result = detector.detect(IMAGE)
    assert result.vehicle_type == "car"
    assert result.confidence == 0.9
    assert result.bbox == (40, 10, 180, 80)
    assert result.plate_bbox == (80, 55, 140, 65)
    assert result.needs_review is False


def test_gemini_supercar_is_supported():
    detector = GeminiVehicleDetector(
        service_with({"vehicle_type": "supercar", "confidence": 0.91, "bbox_1000": None})
    )
    result = detector.detect(IMAGE)
    assert result.vehicle_type == "supercar"
    assert result.needs_review is False


def test_plate_box_away_from_vehicle_is_not_suggested():
    detector = GeminiVehicleDetector(service_with({
        "vehicle_type": "car", "confidence": 0.91,
        "bbox_1000": [350, 200, 900, 850], "plate_bbox_1000": [100, 200, 300, 450],
    }))
    result = detector.detect(IMAGE)
    assert result.vehicle_type == "car"
    assert result.plate_bbox is None


def test_low_confidence_becomes_unknown_and_requires_review():
    detector = GeminiVehicleDetector(
        service_with({"vehicle_type": "car", "confidence": 0.4, "bbox_1000": None})
    )
    result = detector.detect(IMAGE)
    assert result.vehicle_type == "unknown"
    assert result.confidence == 0.4
    assert result.needs_review is True


def test_gemini_requests_and_returns_a_separate_sports_car_type():
    def classify(image, prompt, schema):
        assert schema["properties"]["vehicle_type"]["enum"] == ["car", "sportcar", "supercar", "unknown"]
        assert "sports car" in prompt.split("- sportcar:", 1)[1].split("- supercar:", 1)[0]
        return {"vehicle_type": "sports car", "confidence": 0.9, "bbox_1000": None}

    detector = GeminiVehicleDetector(GeminiJSONService(generate=classify))
    result = detector.detect(IMAGE)
    assert result.vehicle_type == "sportcar"
    assert result.needs_review is False


def test_invalid_service_result_is_explicit():
    detector = GeminiVehicleDetector(service_with({"vehicle_type": "truck", "confidence": 0.9, "bbox_1000": None}))
    result = detector.detect(IMAGE)
    assert result.vehicle_type == "unknown"
    assert "unsupported" in result.error

