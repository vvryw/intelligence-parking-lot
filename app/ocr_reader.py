from __future__ import annotations

import os
import unicodedata
from time import perf_counter

import numpy as np

from .gemini_service import GeminiJSONService
from .schemas import BBox, OCRResult

OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "readable": {"type": "boolean"},
    },
    "required": ["text", "confidence", "readable"],
}

OCR_PROMPT = """Read the license plate text in this cropped image. Transcribe only characters
that are visibly present, preserving Thai letters and numbers. Do not infer missing or blurred
characters. If it is unreadable, return text null and readable false. Return confidence from 0 to 1
for the transcription you supplied."""


def normalize_plate_text(text: str) -> str | None:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFC", text)
        if unicodedata.category(character)[0] in {"L", "M", "N"}
    )
    return normalized or None


class GeminiOCRReader:
    def __init__(self, service: GeminiJSONService | None = None, min_confidence: float | None = None) -> None:
        self.service = service or GeminiJSONService()
        self.min_confidence = min_confidence if min_confidence is not None else float(
            os.getenv("GEMINI_MIN_OCR_CONFIDENCE", "0.60")
        )

    def read(self, image: np.ndarray, bbox: BBox | None) -> OCRResult:
        started = perf_counter()
        try:
            payload = self.service.analyze(image, OCR_PROMPT, OCR_SCHEMA)
            raw_text = payload.get("text")
            text = normalize_plate_text(str(raw_text)) if raw_text is not None else None
            confidence = float(payload["confidence"])
            readable = bool(payload["readable"])
            if not 0 <= confidence <= 1:
                raise ValueError("Gemini confidence is outside 0..1")
            status = "success" if text and readable and confidence >= self.min_confidence else "needs_review"
            error = None if status == "success" else "Gemini OCR is unreadable or below the review threshold"
            return OCRResult(
                text=text,
                confidence=confidence,
                bbox=bbox,
                engine="gemini",
                processing_ms=(perf_counter() - started) * 1000,
                status=status,
                error=error,
            )
        except Exception as exc:
            return OCRResult(
                bbox=bbox,
                engine="gemini",
                processing_ms=(perf_counter() - started) * 1000,
                error=f"Gemini OCR failed: {exc}",
            )
