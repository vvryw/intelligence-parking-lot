from __future__ import annotations

from dataclasses import asdict
from time import perf_counter

import numpy as np

from .ocr_reader import EasyOCRReader, normalize_plate_text
from .openthai_reader import OpenThaiOCRReader
from .schemas import BBox


def edit_distance(expected: str, actual: str) -> int:
    previous = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        current = [i]
        for j, right in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def score_text(expected: str, predicted: str | None) -> dict:
    truth = normalize_plate_text(expected)
    if not truth:
        raise ValueError("Expected plate must contain letters or numbers")
    truth = truth.upper()
    actual = (normalize_plate_text(predicted or "") or "").upper()
    errors = edit_distance(truth, actual)
    return {"expected": truth, "exact_match": actual == truth, "character_errors": errors, "cer": errors / len(truth)}


class OCRComparison:
    def __init__(self, easyocr: EasyOCRReader, openthai: OpenThaiOCRReader | None = None) -> None:
        self.readers = (easyocr, openthai or OpenThaiOCRReader())

    def compare(self, image: np.ndarray, bbox: BBox | None, expected: str | None = None) -> list[dict]:
        if expected:
            score_text(expected, None)  # Validate before doing inference.
        results = []
        # Sequential inference avoids the two engines competing for CPU.
        for reader in self.readers:
            started = perf_counter()
            try:
                reader.warm_up()
                load_ms = (perf_counter() - started) * 1000
                result = asdict(reader.read(image, bbox))
            except Exception as exc:
                result = {"engine": "easyocr" if isinstance(reader, EasyOCRReader) else "openthai-2.0.0",
                          "text": None, "confidence": None, "status": "failed", "error": str(exc),
                          "processing_ms": None}
                load_ms = (perf_counter() - started) * 1000
            result["model_ready_ms"] = round(load_ms, 1)
            result["score"] = score_text(expected, result["text"]) if expected else None
            results.append(result)
        return results
