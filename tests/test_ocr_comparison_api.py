import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main
from app.plate_pipeline import preprocess_plate


@pytest.mark.parametrize("enhance", [False, True])
def test_compare_endpoint_respects_raw_or_enhanced_crop(monkeypatch, enhance):
    original = np.full((100, 200, 3), 220, dtype=np.uint8)
    original[20:40, 30:70] = 10
    _, encoded = cv2.imencode(".png", original)
    observed = []

    def compare(image, bbox, expected):
        observed.append(image.copy())
        assert bbox == (20, 10, 120, 60)
        assert expected == "AB385"
        return []

    monkeypatch.setattr(main.ocr_comparison, "compare", compare)
    with TestClient(main.app) as client:
        response = client.post(
            "/plate/compare", files={"image": ("plate.png", encoded.tobytes(), "image/png")},
            data={"plate_bbox": "20,10,120,60", "expected": "AB385", "enhance": str(enhance).lower()},
        )
    assert response.status_code == 200
    assert response.json()["input_variant"] == ("enhanced" if enhance else "raw")
    if enhance:
        assert observed[0].ndim == 2
        assert observed[0].shape[1] > 480
        assert np.all(observed[0][0] == 255)
    else:
        np.testing.assert_array_equal(observed[0], original[10:60, 20:120])


def test_compare_rejects_box_outside_the_image():
    _, encoded = cv2.imencode(".png", np.zeros((20, 80, 3), dtype=np.uint8))
    with TestClient(main.app) as client:
        response = client.post(
            "/plate/compare", files={"image": ("plate.png", encoded.tobytes(), "image/png")},
            data={"plate_bbox": "100,100,200,200"},
        )
    assert response.status_code == 400


def test_plate_enhancement_whitens_shadows_and_keeps_dark_strokes():
    gray_plate = np.tile(np.linspace(115, 235, 480, dtype=np.uint8), (120, 1))
    gray_plate[20:100, 100:123] //= 2
    gray_plate[20:100, 270:293] //= 2
    enhanced = preprocess_plate(cv2.cvtColor(gray_plate, cv2.COLOR_GRAY2BGR))
    margin = (enhanced.shape[1] - gray_plate.shape[1]) // 2
    inner = enhanced[margin:-margin, margin:-margin]
    assert inner[60, 60] >= 240
    assert inner[60, 410] >= 240
    assert inner[60, 110] <= 60
    assert inner[60, 280] <= 60


def test_api_can_include_paddle_without_changing_crop(monkeypatch):
    original = np.full((60, 100, 3), 220, dtype=np.uint8)
    _, encoded = cv2.imencode(".png", original)
    calls = []

    def compare(image, bbox, expected, **kwargs):
        np.testing.assert_array_equal(image, original[10:40, 20:80])
        calls.append(kwargs)
        return [{"engine": "paddleocr-v5-th", "text": "ME204", "status": "success"}]

    monkeypatch.setattr(main.ocr_comparison, "compare", compare)
    with TestClient(main.app) as client:
        response = client.post(
            "/plate/compare", files={"image": ("plate.png", encoded.tobytes(), "image/png")},
            data={"plate_bbox": "20,10,80,40", "enhance": "false", "include_paddle": "true"},
        )
    assert response.status_code == 200
    assert calls == [{"include_paddle": True}]
    assert response.json()["results"][0]["engine"] == "paddleocr-v5-th"
