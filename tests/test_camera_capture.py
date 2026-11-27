import json
import sys

import cv2
import httpx
import numpy as np
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app import camera_setup, cli, main
from app.image_source import ImageSourceError, fetch_camera_image, load_local_image
from app.schemas import OCRResult, VehicleResult


@pytest.fixture(autouse=True)
def isolate_camera_roles(monkeypatch):
    monkeypatch.delenv("ESP32_ENTRANCE_CAPTURE_URL", raising=False)
    monkeypatch.delenv("ESP32_EXIT_CAPTURE_URL", raising=False)


def test_capture_only_saves_real_decodable_jpeg_without_ai(monkeypatch, tmp_path, capsys):
    encoded, jpeg = cv2.imencode(".jpg", np.zeros((48, 64, 3), dtype=np.uint8))
    assert encoded
    calls = []

    def camera_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, content=jpeg.tobytes(), headers={"content-type": "image/jpeg"}, request=httpx.Request("GET", url))

    def no_ai():
        pytest.fail("Capture-only must not initialize the AI pipeline")

    output = tmp_path / "samples" / "capture.jpg"
    monkeypatch.setattr(httpx, "get", camera_get)
    monkeypatch.setattr(cli, "ParkingAIPipeline", no_ai)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ESP32_CAPTURE_URL", "http://camera.test/capture")
    monkeypatch.setattr(sys, "argv", ["cli", "--camera-url", "--capture-only", str(output)])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "captured"
    assert (result["width"], result["height"]) == (64, 48)
    assert load_local_image(output)[0].shape == (48, 64, 3)
    assert calls == ["http://camera.test/capture"]


def test_camera_timeout_is_actionable(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("Camera unreachable")

    monkeypatch.setattr(httpx, "get", timeout)
    with pytest.raises(ImageSourceError, match="Camera capture failed"):
        fetch_camera_image("http://camera.test/capture")


def test_camera_frames_default_to_upside_down_orientation(monkeypatch):
    sensor_frame = np.zeros((12, 24, 3), dtype=np.uint8)
    sensor_frame[:4, :8] = (255, 0, 0)
    encoded, png = cv2.imencode(".png", sensor_frame)
    assert encoded

    def camera_get(url, **kwargs):
        return httpx.Response(
            200, content=png.tobytes(), headers={"content-type": "image/png"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", camera_get)
    image, _ = fetch_camera_image("http://camera.test/capture")
    np.testing.assert_array_equal(image, sensor_frame[::-1, ::-1])


def test_both_views_turn_upside_down_and_exit_also_mirrors(monkeypatch):
    entrance = "http://entrance.test/capture"
    exit_url = "http://exit.test/capture"
    monkeypatch.setenv("ESP32_ENTRANCE_CAPTURE_URL", entrance)
    monkeypatch.setenv("ESP32_EXIT_CAPTURE_URL", exit_url)
    sensor_frame = np.arange(12 * 24 * 3, dtype=np.uint8).reshape((12, 24, 3))
    encoded, png = cv2.imencode(".png", sensor_frame)
    assert encoded

    def get(url, **kwargs):
        return httpx.Response(200, content=png.tobytes(), headers={"content-type": "image/png"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    previous_entrance_view = sensor_frame[::-1, :]
    previous_exit_view = sensor_frame[:, ::-1]
    incoming, _ = fetch_camera_image(entrance)
    outgoing, _ = fetch_camera_image(exit_url)
    np.testing.assert_array_equal(incoming, previous_entrance_view[::-1, ::-1])
    # Rotate the previous exit view 180 degrees, then mirror it horizontally.
    np.testing.assert_array_equal(outgoing, previous_exit_view[::-1, ::-1][:, ::-1])


def test_camera_rejects_webpage_instead_of_capture(monkeypatch):
    def webpage(url, **kwargs):
        return httpx.Response(200, text="camera homepage", headers={"content-type": "text/html"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", webpage)
    with pytest.raises(ImageSourceError, match="non-image content type"):
        fetch_camera_image("http://camera.test/")


def test_capture_only_rejects_local_image(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli", "--image", "input.jpg", "--capture-only", "output.jpg"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("camera_responds", [True, False])
def test_camera_setup_updates_config_only_after_a_photo(camera_responds, monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=test-key\nESP32_CAPTURE_URL=http://192.168.1.50/capture\n")
    output = tmp_path / "sample.jpg"
    camera_url = "http://192.168.1.51/capture"

    def fetch(url):
        assert url == camera_url
        if not camera_responds:
            raise ImageSourceError("Camera unreachable")
        return np.zeros((48, 64, 3), dtype=np.uint8), "esp32_capture.jpg"

    monkeypatch.setattr(camera_setup, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(camera_setup, "discover_camera_url", lambda *args: camera_url)
    monkeypatch.setattr(camera_setup, "fetch_camera_image", fetch)
    monkeypatch.setattr(sys, "argv", ["camera_setup", "--port", "test-port", "--output", str(output)])
    assert camera_setup.main() == (0 if camera_responds else 1)
    assert output.is_file() == camera_responds
    values = dotenv_values(env)
    assert values["GEMINI_API_KEY"] == "test-key"
    assert values["ESP32_CAPTURE_URL"] == (camera_url if camera_responds else "http://192.168.1.50/capture")


def test_live_preview_stays_local_and_trigger_uses_a_fresh_frame(monkeypatch):
    camera_url = "http://camera.test/capture"
    snapshots = iter([20, 70, None, 150])
    analyzed = []

    def fetch(url, timeout_seconds):
        assert url == camera_url
        value = next(snapshots)
        if value is None:
            raise ImageSourceError("Camera disconnected")
        return np.full((24, 32, 3), value, dtype=np.uint8), "esp32_capture.jpg"

    def analyze(image, image_id, bbox, *, classify_vehicle):
        assert classify_vehicle
        analyzed.append((int(image[0, 0, 0]), image_id, bbox))
        return {"vehicle_type": "car", "license_plate": "กข1234", "status": "success"}

    monkeypatch.setenv("ESP32_CAPTURE_URL", camera_url)
    monkeypatch.setattr(main, "fetch_camera_image", fetch)
    monkeypatch.setattr(main.pipeline, "run_dict", analyze)

    with TestClient(main.app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Simulate sensor alert" in page.text

        preview = client.get("/camera/stream")
        assert preview.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
        frames = preview.content.split(b"--frame\r\n")[1:]
        images = [
            cv2.imdecode(np.frombuffer(frame.split(b"\r\n\r\n", 1)[1], dtype=np.uint8), cv2.IMREAD_COLOR)
            for frame in frames
        ]
        assert [int(image[0, 0, 0]) for image in images] == [20, 70]
        assert analyzed == []

        capture = client.post("/capture")
        assert capture.status_code == 200
        assert capture.json()["license_plate"] == "กข1234"
        assert analyzed == [(150, "esp32_capture.jpg", None)]


def test_web_captures_once_then_classifies_and_reads_only_the_selected_plate(monkeypatch):
    image = np.full((70, 120, 3), 150, dtype=np.uint8)
    calls = []

    def snapshot(url, *, timeout_seconds):
        calls.append("camera")
        return image, "esp32_capture.jpg"

    def classify(decoded):
        calls.append("gemini")
        assert decoded.shape == image.shape
        return VehicleResult(vehicle_type="car", confidence=0.9, needs_review=False, plate_bbox=(10, 30, 90, 50))

    def read_plate(crop, bbox):
        calls.append("easyocr")
        assert crop.shape == (140, 500)
        assert bbox == (10, 30, 90, 50)
        return OCRResult(text="กข1234", confidence=0.9, status="success")

    monkeypatch.setenv("ESP32_CAPTURE_URL", "http://camera.test/capture")
    monkeypatch.setattr(main, "fetch_camera_image", snapshot)
    monkeypatch.setattr(main, "warm_ocr", lambda: None)
    monkeypatch.setattr(main.pipeline.vehicle_detector, "detect", classify)
    monkeypatch.setattr(main.pipeline.ocr_reader, "read", read_plate)

    with TestClient(main.app) as client:
        photo = client.get("/camera/snapshot")
        assert photo.status_code == 200
        assert float(photo.headers["x-camera-ms"]) >= 0
        assert calls == ["camera"]
        upload = {"image": ("capture.jpg", photo.content, "image/jpeg")}
        vehicle = client.post("/classify", files=upload)
        assert vehicle.json()["vehicle_type"] == "car"
        suggested = vehicle.json()["plate_bbox"]
        assert suggested == [10, 30, 90, 50]
        assert calls == ["camera", "gemini"]

        invalid = client.post("/plate/read", files=upload, data={"plate_bbox": "150,30,200,50"})
        assert invalid.status_code == 400
        plate = client.post("/plate/read", files=upload, data={"plate_bbox": ",".join(map(str, suggested))})
        assert plate.json()["license_plate"] == "กข1234"
        assert plate.json()["enhanced_plate"].startswith("data:image/jpeg;base64,")
        assert calls == ["camera", "gemini", "easyocr"]


def test_two_cameras_keep_their_urls_and_capture_labels(monkeypatch):
    monkeypatch.setenv("ESP32_ENTRANCE_CAPTURE_URL", "http://entrance.test/capture")
    monkeypatch.setenv("ESP32_EXIT_CAPTURE_URL", "http://exit.test/capture")
    monkeypatch.setattr(main, "warm_ocr", lambda: None)
    calls = []

    def fetch(url, timeout_seconds):
        calls.append(url)
        return np.full((24, 32, 3), 80 if "entrance" in url else 170, dtype=np.uint8), "capture.jpg"

    monkeypatch.setattr(main, "fetch_camera_image", fetch)
    def analyze(image, image_id, bbox, *, classify_vehicle):
        pixel = int(image[0, 0, 0])
        assert classify_vehicle == (pixel == 80)
        return {"pixel": pixel}

    monkeypatch.setattr(main.pipeline, "run_dict", analyze)
    with TestClient(main.app) as client:
        for role, value in [("entrance", 80), ("exit", 170)]:
            photo = client.get("/camera/snapshot", params={"camera": role})
            assert photo.status_code == 200
            assert photo.headers["x-camera-role"] == role
            assert calls[-1] == f"http://{role}.test/capture"
            result = client.post("/capture", data={"camera": role})
            assert result.json() == {"camera": role, "pixel": value}
        monkeypatch.delenv("ESP32_EXIT_CAPTURE_URL")
        assert client.get("/camera/snapshot?camera=exit").status_code == 503
        assert client.get("/camera/snapshot?camera=invalid").status_code == 422


def test_exit_capture_and_plate_read_never_classify(monkeypatch):
    image = np.zeros((200, 320, 3), dtype=np.uint8)
    cv2.rectangle(image, (100, 100), (216, 130), (255, 255, 255), -1)
    _, photo = cv2.imencode(".jpg", image)

    def unexpected_gemini(_):
        pytest.fail("Exit frames must never be classified")

    box_calls = []

    def locate_plate(frame):
        box_calls.append(frame.shape)
        if frame.max() == 0:
            return None, "test-gemini"
        return (100, 100, 217, 131), "test-gemini"

    def read_plate(crop, bbox):
        assert bbox is not None
        assert crop.size
        return OCRResult(text="กข1234", confidence=0.9, bbox=bbox, status="success")

    monkeypatch.setenv("ESP32_EXIT_CAPTURE_URL", "http://exit.test/capture")
    monkeypatch.setenv("GEMINI_EXIT_PLATE_BOX", "true")
    monkeypatch.setattr(main, "fetch_camera_image", lambda *args, **kwargs: (image, "exit.jpg"))
    monkeypatch.setattr(main.pipeline.vehicle_detector, "detect", unexpected_gemini)
    monkeypatch.setattr(main.pipeline.vehicle_detector, "locate_plate", locate_plate)
    monkeypatch.setattr(main.pipeline.ocr_reader, "read", read_plate)

    upload = {"image": ("exit.jpg", photo.tobytes(), "image/jpeg")}
    exit_form = {"camera": "exit"}
    with TestClient(main.app) as client:
        for response in (client.post("/capture", data=exit_form), client.post("/infer", files=upload, data=exit_form)):
            assert response.status_code == 200
            result = response.json()
            assert result["license_plate"] == "กข1234"
            assert result["status"] == "success"
            assert result["vehicle_type"] is None
            assert result["processing_ms"]["vehicle"] is None
            assert result["plate_box"]["source"] == "gemini"

        automatic = client.post("/plate/read", files=upload, data=exit_form)
        assert automatic.status_code == 200
        assert automatic.json()["license_plate"] == "กข1234"
        assert automatic.json()["bbox"]
        assert automatic.json()["plate_box"]["model"] == "test-gemini"
        assert len(box_calls) == 3

        selected = client.post("/plate/read", files=upload, data={**exit_form, "plate_bbox": "100,100,217,131"})
        assert selected.json()["plate_box"]["source"] == "manual"
        assert len(box_calls) == 3
        assert client.post("/classify", files=upload, data=exit_form).status_code == 400

        blank = cv2.imencode(".jpg", np.zeros_like(image))[1].tobytes()
        unclear = client.post("/plate/read", files={"image": ("blank.jpg", blank, "image/jpeg")}, data=exit_form)
        assert unclear.status_code == 200
        assert unclear.json()["status"] == "needs_review"
        assert unclear.json()["enhanced_plate"] is None
        assert unclear.json()["plate_box"]["source"] == "heuristic_fallback"
