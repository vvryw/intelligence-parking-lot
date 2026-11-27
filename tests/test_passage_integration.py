"""Focused API/worker checks with simulated transport and a test JPEG."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.main import decode_upload
from app.parking_api import parking_router
from app.parking_recognition import ParkingRecognition
from app.passage_api import passage_router
from app.passage_store import PassageParkingStore
from app.pipeline import ParkingAIPipeline
from app.schemas import OCRResult, VehicleResult
from scripts.run_passage_worker import ACK, ARRIVAL, COMMAND, FEEDBACK, FEEDBACK_ACK, PassageWorker


def now():
    return datetime.now(timezone.utc)


def event(aid, **payload):
    return {"schema_version": 1, "event_id": uuid4().hex, "producer_id": "sensor-gateway",
            "occurred_at": now().isoformat(), "arrival_id": aid, **payload}


def arrival(aid, camera):
    return event(aid, direction=camera, trigger="ultrasonic")


def feedback(aid, direction, command_id=None, *, success=True):
    return event(aid, direction=direction, command_id=command_id, state="completed" if success else "hold",
                 passage_confirmed=success, simulation=True, reason=None)


class TestClock:
    __test__ = False

    def verified(self):
        return True

    def __call__(self):
        return now()


class RecognitionFixture:
    engine = "test-ocr"
    min_confidence = 0.6

    def detect(self, image):
        return VehicleResult(vehicle_type="car", confidence=0.9, needs_review=False, plate_bbox=(4, 4, 40, 20))

    def read(self, image, bbox):
        return OCRResult(text="ME204", confidence=.9, bbox=bbox, status="success")


@pytest.fixture
def joint(tmp_path, monkeypatch):
    monkeypatch.setenv("PARKING_PASSAGE_KEY", "only-test-key")
    monkeypatch.setenv("GEMINI_EXIT_PLATE_BOX", "false")
    recognition = RecognitionFixture()
    pipeline = ParkingAIPipeline(vehicle_detector=recognition, ocr_reader=recognition)
    store = PassageParkingStore(tmp_path / "parking.sqlite3", tmp_path / "photos")
    app = FastAPI()
    app.include_router(passage_router(store))
    app.include_router(parking_router(ParkingRecognition(pipeline, store), decode_upload))
    frames, published = [], []
    frame = np.zeros((60, 90, 3), np.uint8)
    cv2.rectangle(frame, (4, 4), (40, 20), (255, 255, 255), -1)
    jpeg = cv2.imencode(".jpg", frame)[1].tobytes()

    @app.get("/camera/snapshot")
    def snapshot(camera: str):
        frames.append(camera)
        return Response(jpeg, media_type="image/jpeg")

    with TestClient(app, headers={"X-Passage-Key": "only-test-key"}) as http:
        worker = PassageWorker(http, lambda topic, body: published.append((topic, json.loads(body))), TestClock(), {})
        worker.preflight()
        yield http, worker, frames, published, store


def wire(worker, topic, message):
    worker.receive(topic, json.dumps(message).encode(), False)


def reserve(worker, published, aid="entrance-1"):
    message = arrival(aid, "entrance")
    wire(worker, ARRIVAL, message)
    worker.tick()
    topic, command = published[-1]
    assert topic == COMMAND
    return message, command


def lane_status(http, aid):
    result = http.get(f"/parking/passage/arrivals/{aid}")
    result.raise_for_status()
    return result.json()


def bay(http):
    data = http.get("/parking/status").json()
    return next(item for item in data["spaces"] if item["id"] == "NORMAL-1")


def test_entrance_reserves_before_open_and_finalizes_after_confirmed_feedback(joint):
    http, worker, _, published, store = joint
    _, command = reserve(worker, published)
    assert bay(http)["status"] == "reserved"
    assert http.get("/parking/history").json()["sessions"] == []
    assert datetime.fromisoformat(command["expires_at"]) - datetime.fromisoformat(command["issued_at"]) == timedelta(seconds=5)
    ack = event(command["arrival_id"], ack_id="receipt-1", command_id=command["command_id"], state="accepted",
                status="accepted", reason="simulated_open_lcd", physical_movement=False)
    wire(worker, ACK, ack)
    assert lane_status(http, "entrance-1")["ack_state"] == "accepted"
    assert bay(http)["status"] == "reserved"
    wire(worker, FEEDBACK, feedback("entrance-1", "entrance", command["command_id"], success=False))
    assert bay(http)["status"] == "review"
    proof = feedback("entrance-1", "entrance", command["command_id"])
    wire(worker, FEEDBACK, proof)
    assert bay(http)["status"] == "occupied"
    sessions = http.get("/parking/history").json()["sessions"]
    assert len(sessions) == 1 and sessions[0]["id"] == command["session_id"]
    receipt = published[-1]
    assert receipt[0] == FEEDBACK_ACK and receipt[1]["feedback_id"] == proof["event_id"]
    # Reopening the ledger returns the same durable receipt after a restart.
    reopened = PassageParkingStore(store.database, store.storage)
    restarted = FastAPI()
    restarted.include_router(passage_router(reopened))
    with TestClient(restarted, headers={"X-Passage-Key": "only-test-key"}) as client:
        retry = client.post("/parking/passage/gate/feedback", json=proof)
        assert retry.json()["receipt"] == receipt[1]


def test_duplicate_entrance_has_one_snapshot_and_command_and_rejection_releases_hold(joint):
    http, worker, frames, published, _ = joint
    message, command = reserve(worker, published)
    wire(worker, ARRIVAL, message)
    worker.tick()
    assert frames == ["entrance"]
    assert [body for topic, body in published if topic == COMMAND] == [command]
    row = lane_status(http, "entrance-1")
    prepare = http.post("/parking/passage/arrivals/entrance-1/prepare",
                        json={"observation_id": row["observation_id"]}).json()
    assert prepare["replayed"] and prepare["command"] is None
    assert http.post("/parking/commit", json={"observation_id": row["observation_id"]}).status_code == 409
    assert http.post("/parking/passage/arrivals/entrance-1/cancel", json={"confirmed_no_passage": True}).status_code == 409
    unknown = event("entrance-1", ack_id="unknown-1", command_id=command["command_id"], state="unknown", reason=None)
    wire(worker, ACK, unknown)
    assert bay(http)["status"] == "review"

    # Resolve the first reservation after the original command's deadline.
    with patch("app.passage_store.datetime", wraps=datetime) as clock:
        clock.now.return_value = now() + timedelta(seconds=7)
        result = http.post("/parking/passage/arrivals/entrance-1/cancel", json={"confirmed_no_passage": True})
    assert result.json()["state"] == "cancelled"
    _, second = reserve(worker, published, "entrance-2")
    rejected = event("entrance-2", ack_id="reject-1", command_id=second["command_id"], state="rejected",
                     reason="uno_rejected")
    wire(worker, ACK, rejected)
    assert lane_status(http, "entrance-2")["state"] == "cancelled"
    assert bay(http)["status"] == "free"


def test_exit_waits_for_matching_passage_and_never_releases_a_later_visit(joint):
    http, worker, _, published, _ = joint
    _, command = reserve(worker, published)
    wire(worker, FEEDBACK, feedback("entrance-1", "entrance", command["command_id"]))

    wire(worker, ARRIVAL, arrival("exit-1", "exit"))
    assert lane_status(http, "exit-1")["session_id"] == command["session_id"]
    assert bay(http)["status"] == "occupied"
    wire(worker, FEEDBACK, feedback("unrelated", "exit"))
    wire(worker, FEEDBACK, feedback("exit-1", "exit", success=False))
    assert bay(http)["status"] == "occupied"
    proof = feedback("exit-1", "exit")
    wire(worker, FEEDBACK, proof)
    assert bay(http)["status"] == "free"
    assert lane_status(http, "exit-1")["state"] == "completed"
    wire(worker, FEEDBACK, proof)
    assert len([item for item in http.get("/parking/history").json()["events"] if item["camera"] == "exit"]) == 1

    _, newer = reserve(worker, published, "entrance-2")
    wire(worker, FEEDBACK, feedback("entrance-2", "entrance", newer["command_id"]))
    assert bay(http)["session_id"] == newer["session_id"]
    wire(worker, FEEDBACK, feedback("exit-1", "exit"))
    assert bay(http)["session_id"] == newer["session_id"]

    early_arrival = arrival("exit-2", "exit")
    wire(worker, FEEDBACK, feedback("exit-2", "exit"))
    assert bay(http)["status"] == "occupied"
    wire(worker, ARRIVAL, early_arrival)
    assert lane_status(http, "exit-2")["state"] == "completed"
    assert bay(http)["status"] == "free"
    assert all(body["direction"] == "entrance" for topic, body in published if topic == COMMAND)
