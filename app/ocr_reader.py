from __future__ import annotations

import os
import unicodedata
from threading import Lock
from time import perf_counter
from typing import Any, Protocol

import numpy as np

from .schemas import BBox, OCRResult


def normalize_plate_text(text: str) -> str | None:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFC", text)
        if unicodedata.category(character)[0] in {"L", "M", "N"}
    )
    return normalized or None


class OCRReader(Protocol):
    engine: str

    def warm_up(self) -> None: ...

    def read(self, image: np.ndarray, bbox: BBox | None) -> OCRResult: ...


class EasyOCRReader:
    """Local Thai/English OCR; load and reuse one CPU reader on first use."""

    engine = "easyocr"

    def __init__(self, reader: Any = None, min_confidence: float | None = None) -> None:
        self._reader = reader
        # Retain custom thresholds from older .env files during migration.
        self.min_confidence = min_confidence if min_confidence is not None else float(
            os.getenv("EASYOCR_MIN_CONFIDENCE", os.getenv("GEMINI_MIN_OCR_CONFIDENCE", "0.60"))
        )
        self._lock = Lock()

    def _load_reader(self) -> None:
        if self._reader is None:
            try:
                import easyocr
            except ImportError as exc:
                raise RuntimeError("EasyOCR is not installed; install requirements-ocr-compare.txt") from exc
            self._reader = easyocr.Reader(["th", "en"], gpu=False, verbose=False)

    def warm_up(self) -> None:
        with self._lock:
            self._load_reader()

    def read(self, image: np.ndarray, bbox: BBox | None) -> OCRResult:
        started = perf_counter()
        try:
            # API requests run in worker threads. Serialize model initialization
            # and inference so concurrent captures share the reader safely.
            with self._lock:
                self._load_reader()
                detections = self._reader.readtext(image, detail=1, paragraph=False)
            texts: list[str] = []
            confidences: list[float] = []
            for _, raw_text, raw_confidence in detections:
                normalized = normalize_plate_text(raw_text)
                if not normalized:
                    continue
                confidence = float(raw_confidence)
                if not 0 <= confidence <= 1:
                    raise ValueError("EasyOCR confidence is outside 0..1")
                texts.append(normalized)
                confidences.append(confidence)
            text = normalize_plate_text("".join(texts))
            # One uncertain text fragment must not be hidden by stronger ones.
            confidence = min(confidences) if confidences else None
            status = "success" if text and confidence is not None and confidence >= self.min_confidence else "needs_review"
            error = None if status == "success" else "EasyOCR found no readable text or confidence is below the review threshold"
            return OCRResult(
                text=text,
                confidence=confidence,
                bbox=bbox,
                engine="easyocr",
                processing_ms=(perf_counter() - started) * 1000,
                status=status,
                error=error,
            )
        except Exception as exc:
            return OCRResult(
                bbox=bbox,
                engine="easyocr",
                processing_ms=(perf_counter() - started) * 1000,
                error=f"EasyOCR failed: {exc}",
            )
