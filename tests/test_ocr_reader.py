import numpy as np

from app.gemini_service import GeminiJSONService
from app.ocr_reader import GeminiOCRReader, normalize_plate_text


def service_with(payload):
    return GeminiJSONService(generate=lambda image, prompt, schema: payload)


def test_normalization_preserves_thai_marks_and_digits():
    assert normalize_plate_text(" ก๋ข-1234 ") == "ก๋ข1234"


def test_gemini_ocr_result_is_normalized():
    reader = GeminiOCRReader(service_with({"text": "กข 1234", "confidence": 0.9, "readable": True}))
    result = reader.read(np.zeros((20, 80), dtype=np.uint8), (1, 2, 10, 20))
    assert result.text == "กข1234"
    assert result.confidence == 0.9
    assert result.engine == "gemini"
    assert result.status == "success"


def test_unreadable_ocr_requires_review():
    reader = GeminiOCRReader(service_with({"text": None, "confidence": 0.2, "readable": False}))
    result = reader.read(np.zeros((20, 80), dtype=np.uint8), None)
    assert result.text is None
    assert result.status == "needs_review"
    assert result.error

