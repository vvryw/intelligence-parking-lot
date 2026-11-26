from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import decode_upload
from app.parking_api import parking_router
from app.parking_recognition import ParkingRecognition
from app.parking_store import ParkingStore
from app.pipeline import ParkingAIPipeline
from app.schemas import OCRResult, VehicleResult


@pytest.fixture
def setup(tmp_path):
    class Vehicle:
        calls = 0

        def detect(self, image):
            self.calls += 1
            return VehicleResult(vehicle_type="sportcar", confidence=.9, needs_review=False, plate_bbox=(5, 5, 60, 25))

    class OCR:
        engine = "paddleocr-v5-th"
        min_confidence = .6

        def read(self, image, bbox):
            return OCRResult(text="AB385", confidence=.95, engine=self.engine, status="success", bbox=bbox)

    pipeline = ParkingAIPipeline(vehicle_detector=Vehicle(), ocr_reader=OCR())
    store = ParkingStore(tmp_path / "db", tmp_path / "photos")
    service = ParkingRecognition(pipeline, store)
    app = FastAPI()
    app.include_router(parking_router(service, decode_upload))
    _, encoded = cv2.imencode(".png", np.full((40, 80, 3), 180, np.uint8))
    return app, service, encoded.tobytes()


def test_real_api_flow_archives_allocates_replays_and_releases(setup):
    app, service, image = setup
    with TestClient(app) as client:
        observation = client.post("/parking/analyze", files={"image": ("car.png", image)}, data={"camera": "entrance"}).json()
        assert observation["eligible"]
        assert client.get(observation["photo_url"]).headers["content-type"] == "image/jpeg"
        assert client.get("/parking/status").json()["capacity"]["vip"]["available"] == 2
        # Arbitrary browser-provided plates/classifications cannot replace server recognition.
        assert client.post("/parking/commit", json={"observation_id": observation["observation_id"], "vehicle_type": "car"}).status_code == 422
        first = client.post("/parking/commit", json={"observation_id": observation["observation_id"]}).json()
        assert first["decision"]["space_id"] == "VIP-1"
        replay = client.post("/parking/commit", json={"observation_id": observation["observation_id"]}).json()
        assert replay["decision"]["replayed"]
        exit_obs = client.post("/parking/analyze", files={"image": ("car.png", image)}, data={"camera": "exit", "plate_bbox": "5,5,60,25"}).json()
        assert service.pipeline.vehicle_detector.calls == 1  # Exit never calls Gemini.
        result = client.post("/parking/commit", json={"observation_id": exit_obs["observation_id"]}).json()
        assert result["decision"]["approved"]
        assert result["parking"]["capacity"]["vip"]["available"] == 2


def test_crop_correction_reuses_classification_but_rejects_another_photo(setup):
    app, service, image = setup
    with TestClient(app) as client:
        original = client.post("/parking/analyze", files={"image": ("car.png", image)}).json()
        data = {"previous_observation_id": original["observation_id"], "plate_bbox": "6,5,60,25"}
        assert client.post("/parking/analyze", files={"image": ("car.png", image)}, data=data).status_code == 200
        assert service.pipeline.vehicle_detector.calls == 1
        _, another = cv2.imencode(".png", np.zeros((40, 80, 3), np.uint8))
        assert client.post("/parking/analyze", files={"image": ("other.png", another.tobytes())}, data=data).status_code == 400


def test_missing_analysis_and_cloud_errors_fail_closed(setup, monkeypatch):
    app, service, _ = setup
    with TestClient(app) as client:
        assert client.post("/parking/commit", json={"observation_id": str(uuid4())}).status_code == 404
        monkeypatch.setattr(service.store, "commit", lambda *args: (_ for _ in ()).throw(OSError("cloud offline")))
        assert client.post("/parking/commit", json={"observation_id": str(uuid4())}).status_code == 503


def test_sensor_endpoint_requires_key_and_strict_occupancy(setup, monkeypatch):
    app, service, _ = setup
    calls = []
    monkeypatch.setenv("SENSOR_INTEGRATION_TOKEN", "test-secret")
    monkeypatch.setattr(service.store, "record_sensors", lambda updates, source: calls.append(updates) or {"accepted": 1}, raising=False)
    batch = {"source": "sensor-dashboard", "spaces": [{"space_id": "NORMAL-1", "occupied": False, "observed_at": "2026-11-26T15:00:00+00:00"}]}
    with TestClient(app) as client:
        assert client.post("/parking/sensors", json=batch).status_code == 401
        assert client.post("/parking/sensors", json=batch, headers={"X-Integration-Key": "test-secret"}).status_code == 200
        batch["spaces"][0]["occupied"] = "false"
        assert client.post("/parking/sensors", json=batch, headers={"X-Integration-Key": "test-secret"}).status_code == 422
    assert calls[0][0]["occupied"] is False
