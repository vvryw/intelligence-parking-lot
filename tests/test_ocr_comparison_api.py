import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main


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
        assert observed[0].shape[1] == 480
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
