import numpy as np
import pytest

from app.plate_pipeline import PlateCrop
from app.gemini_service import GeminiJSONService
from app.ocr_reader import EasyOCRReader
from app.paddleocr_reader import PaddleOCRReader
from app.pipeline import ParkingAIPipeline
from app.schemas import OCRResult, VehicleResult
from app.vehicle_detector import GeminiVehicleDetector


class StubVehicle:
    def detect(self, image):
        return VehicleResult("car", 0.88, (1, 2, 80, 90), 4.0, False)


class StubPlate:
    def extract(self, image, manual_bbox=None):
        return PlateCrop(np.zeros((20, 60), dtype=np.uint8), (10, 50, 70, 70), "manual")


class StubOCR:
    def read(self, image, bbox):
        return OCRResult("กข1234", 0.81, bbox, "easyocr", 3.0, "success")


def test_default_pipeline_uses_paddleocr_for_thai_and_english():
    pipeline = ParkingAIPipeline(vehicle_detector=StubVehicle())
    assert isinstance(pipeline.ocr_reader, PaddleOCRReader)
    assert pipeline.ocr_reader.language == "th"


def test_combined_result_contract():
    pipeline = ParkingAIPipeline(StubVehicle(), StubPlate(), StubOCR())
    result = pipeline.run(
        np.zeros((100, 100, 3), dtype=np.uint8), "sample.jpg", (10, 50, 70, 70)
    ).to_dict()
    assert result["vehicle_type"] == "car"
    assert result["license_plate"] == "กข1234"
    assert result["ocr_engine"] == "easyocr"
    assert result["status"] == "success"
    assert result["processing_ms"]["total"] >= 0


def test_api_ready_pipeline_result_is_a_dictionary():
    pipeline = ParkingAIPipeline(StubVehicle(), StubPlate(), StubOCR())
    result = pipeline.run_dict(np.zeros((100, 100, 3), dtype=np.uint8), "sample.jpg")
    assert isinstance(result, dict)
    assert result["image_id"] == "sample.jpg"


def test_gemini_classification_and_local_ocr_use_separate_engines():
    gemini_calls = []

    def classify(image, prompt, schema):
        gemini_calls.append(prompt)
        return {"vehicle_type": "car", "confidence": 0.95, "bbox_1000": None}

    class LocalReader:
        def readtext(self, image, **kwargs):
            return [([[0, 0], [20, 0], [20, 10], [0, 10]], "กข1234", 0.9)]

    pipeline = ParkingAIPipeline(
        GeminiVehicleDetector(GeminiJSONService(generate=classify)),
        StubPlate(),
        EasyOCRReader(LocalReader()),
    )
    result = pipeline.run_dict(np.zeros((100, 100, 3), dtype=np.uint8), "camera.jpg")
    assert len(gemini_calls) == 1
    assert result["vehicle_type"] == "car"
    assert result["license_plate"] == "กข1234"
    assert result["ocr_engine"] == "easyocr"
    assert result["status"] == "success"


@pytest.mark.parametrize(
    ("enhanced_text", "raw_text", "text", "status"),
    [
        ("ม835", "จม835", "จม835", "success"),
        ("AB385", "AB", "AB385", "success"),
        ("AB385", "AC385", "AB385", "needs_review"),
        (None, "483S5", "483S5", "needs_review"),
    ],
)
def test_exit_box_compares_raw_and_enhanced_readings(enhanced_text, raw_text, text, status):
    class Reader:
        def read(self, image, bbox):
            value = raw_text if image.ndim == 3 else enhanced_text
            return OCRResult(text=value, confidence=.9 if value else None,
                             status="success" if value else "needs_review", bbox=bbox)

    image = np.zeros((100, 100, 3), dtype=np.uint8)
    crop = PlateCrop(np.zeros((20, 60), dtype=np.uint8), (10, 50, 70, 70), "gemini")
    result = ParkingAIPipeline(ocr_reader=Reader()).read_cropped_plate(image, crop)
    assert (result.text, result.status) == (text, status)
    if text == raw_text:
        assert crop.image.ndim == 3
