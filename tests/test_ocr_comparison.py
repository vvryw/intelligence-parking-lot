import numpy as np
import pytest

from app.ocr_comparison import OCRComparison, edit_distance, score_text
from app.openthai_reader import OpenThaiOCRReader
from app.schemas import OCRResult


def test_plate_scoring_ignores_formatting_and_latin_case_but_keeps_thai_marks():
    assert score_text("AB-385", "ab 385")["exact_match"]
    assert score_text("กห6000", "กห 6000")["cer"] == 0
    assert score_text("ส6767", "สี6767")["character_errors"] == 1
    assert score_text("AB385", None)["cer"] == 1
    assert edit_distance("", "AB") == 2
    with pytest.raises(ValueError):
        score_text(" - ", "AB")


def test_comparison_uses_identical_pixels_and_scores_each_engine_even_if_one_fails():
    image = np.zeros((20, 80), dtype=np.uint8)

    class Working:
        def warm_up(self):
            pass

        def read(self, supplied, bbox):
            assert supplied is image
            return OCRResult(text="AB385", bbox=bbox, status="success")

    class Unavailable:
        def warm_up(self):
            raise RuntimeError("Model missing")

    results = OCRComparison(Working(), Unavailable()).compare(image, (1, 2, 3, 4), "AB385")
    assert results[0]["score"]["exact_match"]
    assert results[1]["status"] == "failed"
    assert "Model missing" in results[1]["error"]
    assert results[1]["score"]["cer"] == 1


def test_openthai_preserves_low_confidence_output_for_review():
    class LowConfidence:
        def read(self, image):
            return {"text": "กห 6000", "lines": [{"text": "กห 6000", "confidence": 0.3}]}

    result = OpenThaiOCRReader(LowConfidence()).read(np.zeros((20, 80), dtype=np.uint8), None)
    assert result.text == "กห6000"
    assert result.status == "needs_review"
    assert result.engine == "openthai-2.0.0"
