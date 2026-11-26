import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.ocr_reader import EasyOCRReader, normalize_plate_text


class FakeReader:
    def __init__(self, detections):
        self.detections = detections

    def readtext(self, image, **kwargs):
        assert kwargs == {"detail": 1, "paragraph": False}
        return self.detections


def detection(text, confidence):
    return ([[0, 0], [20, 0], [20, 10], [0, 10]], text, confidence)


def test_normalization_preserves_thai_marks_and_digits():
    assert normalize_plate_text(" ก๋ข-1234 ") == "ก๋ข1234"


def test_easyocr_joins_and_normalizes_fragments_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reader = EasyOCRReader(FakeReader([detection(" ก๋ข ", 0.9), detection("12-34", 0.8)]))
    result = reader.read(np.zeros((20, 80), dtype=np.uint8), (1, 2, 10, 20))
    assert result.text == "ก๋ข1234"
    assert result.confidence == 0.8
    assert result.bbox == (1, 2, 10, 20)
    assert result.engine == "easyocr"
    assert result.status == "success"


def test_unreadable_ocr_requires_review():
    reader = EasyOCRReader(FakeReader([]))
    result = reader.read(np.zeros((20, 80), dtype=np.uint8), None)
    assert result.text is None
    assert result.confidence is None
    assert result.status == "needs_review"
    assert result.error


def test_uncertain_fragment_keeps_full_transcription_for_review():
    reader = EasyOCRReader(FakeReader([detection("กข", 0.99), detection("1234", 0.3)]))
    result = reader.read(np.zeros((20, 80), dtype=np.uint8), None)
    assert result.text == "กข1234"
    assert result.confidence == 0.3
    assert result.status == "needs_review"


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan")])
def test_invalid_confidence_returns_explicit_failure(confidence):
    reader = EasyOCRReader(FakeReader([detection("1234", confidence)]))
    result = reader.read(np.zeros((20, 80), dtype=np.uint8), None)
    assert result.status == "failed"
    assert result.engine == "easyocr"
    assert "outside 0..1" in result.error


def test_reader_is_lazy_and_reused(monkeypatch):
    calls = []

    def create_reader(languages, **kwargs):
        calls.append((languages, kwargs))
        return FakeReader([detection("กข1234", 0.9)])

    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=create_reader))
    reader = EasyOCRReader()
    assert not calls
    for _ in range(2):
        assert reader.read(np.zeros((20, 80), dtype=np.uint8), None).status == "success"
    assert calls == [(["th", "en"], {"gpu": False, "verbose": False})]


def test_model_loading_failure_returns_explicit_error(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Model download unavailable")

    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=unavailable))
    result = EasyOCRReader().read(np.zeros((20, 80), dtype=np.uint8), None)
    assert result.status == "failed"
    assert "Model download unavailable" in result.error


def test_easyocr_threshold_overrides_legacy_setting(monkeypatch):
    monkeypatch.setenv("GEMINI_MIN_OCR_CONFIDENCE", "0.9")
    monkeypatch.setenv("EASYOCR_MIN_CONFIDENCE", "0.7")
    assert EasyOCRReader().min_confidence == 0.7

