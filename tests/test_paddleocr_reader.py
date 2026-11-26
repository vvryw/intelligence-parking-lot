from types import SimpleNamespace

import numpy as np
import pytest

from app.paddleocr_reader import PaddleOCRReader


def test_paddleocr_preserves_thai_and_combines_fragments_with_lowest_confidence():
    class Model:
        def __call__(self, image, **kwargs):
            assert kwargs == {"use_det": True, "use_cls": False, "use_rec": True}
            return SimpleNamespace(txts=("จม", "835"), scores=(.9, .3))

    result = PaddleOCRReader(reader=Model()).read(np.zeros((30, 100, 3), np.uint8), (1, 2, 3, 4))
    assert result.text == "จม835"
    assert result.confidence == .3
    assert result.status == "needs_review"
    assert result.bbox == (1, 2, 3, 4)


def test_empty_detection_requires_review():
    reader = PaddleOCRReader(reader=lambda image, **kwargs: SimpleNamespace(txts=None, scores=None))
    assert reader.read(np.zeros((30, 100), np.uint8), None).status == "needs_review"


@pytest.mark.parametrize("score", [-.1, 1.1, float("nan")])
def test_invalid_model_confidence_is_a_failure(score):
    reader = PaddleOCRReader(reader=lambda image, **kwargs: SimpleNamespace(txts=("ME204",), scores=(score,)))
    result = reader.read(np.zeros((30, 100), np.uint8), None)
    assert result.status == "failed"
    assert "outside 0..1" in result.error


def test_english_profile_is_explicit():
    assert PaddleOCRReader("en").engine == "paddleocr-v5-en"
    with pytest.raises(ValueError):
        PaddleOCRReader("auto")


def test_paddle_review_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("PADDLEOCR_MIN_CONFIDENCE", "0.95")
    reader = PaddleOCRReader(reader=lambda image, **kwargs: SimpleNamespace(txts=("ME204",), scores=(.90,)))
    result = reader.read(np.zeros((30, 100), np.uint8), None)
    assert result.text == "ME204"
    assert result.status == "needs_review"
