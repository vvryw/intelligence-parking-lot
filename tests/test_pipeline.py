import numpy as np

from app.plate_pipeline import PlateCrop
from app.pipeline import ParkingAIPipeline
from app.schemas import OCRResult, VehicleResult


class StubVehicle:
    def detect(self, image):
        return VehicleResult("sportcar", 0.88, (1, 2, 80, 90), 4.0, False)


class StubPlate:
    def extract(self, image, manual_bbox=None):
        return PlateCrop(np.zeros((20, 60), dtype=np.uint8), (10, 50, 70, 70), "manual")


class StubOCR:
    def read(self, image, bbox):
        return OCRResult("กข1234", 0.81, bbox, "gemini", 3.0, "success")


def test_combined_result_contract():
    pipeline = ParkingAIPipeline(StubVehicle(), StubPlate(), StubOCR())
    result = pipeline.run(
        np.zeros((100, 100, 3), dtype=np.uint8), "sample.jpg", (10, 50, 70, 70)
    ).to_dict()
    assert result["vehicle_type"] == "sportcar"
    assert result["license_plate"] == "กข1234"
    assert result["ocr_engine"] == "gemini"
    assert result["status"] == "success"
    assert result["processing_ms"]["total"] >= 0


def test_api_ready_pipeline_result_is_a_dictionary():
    pipeline = ParkingAIPipeline(StubVehicle(), StubPlate(), StubOCR())
    result = pipeline.run_dict(np.zeros((100, 100, 3), dtype=np.uint8), "sample.jpg")
    assert isinstance(result, dict)
    assert result["image_id"] == "sample.jpg"
