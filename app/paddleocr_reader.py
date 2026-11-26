from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Literal

import numpy as np

from .ocr_reader import normalize_plate_text
from .schemas import BBox, OCRResult

PADDLE_MODEL_DIR = Path(__file__).resolve().parents[1] / "models/paddleocr-v5-mobile"


class PaddleOCRReader:
    """PP-OCRv5 mobile detector/recognizer on one ONNX Runtime CPU thread."""

    def __init__(self, language: Literal["th", "en"] = "th", reader: Any = None) -> None:
        if language not in {"th", "en"}:
            raise ValueError("PaddleOCR language must be th or en")
        self.language = language
        self.engine = f"paddleocr-v5-{language}"
        self.min_confidence = float(os.getenv("PADDLEOCR_MIN_CONFIDENCE", "0.60"))
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("PADDLEOCR_MIN_CONFIDENCE must be between 0 and 1")
        self._reader = reader
        self._lock = Lock()

    def _load_reader(self) -> None:
        if self._reader is not None:
            return
        det = PADDLE_MODEL_DIR / "ch_PP-OCRv5_det_mobile.onnx"
        rec = PADDLE_MODEL_DIR / f"{self.language}_PP-OCRv5_rec_mobile.onnx"
        if not det.is_file() or not rec.is_file():
            raise RuntimeError("Run python scripts/setup_paddleocr.py to install the pinned models")
        from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR

        candidate = RapidOCR(params={
            "Global.use_cls": False,
            "Global.log_level": "warning",
            "Global.model_root_dir": str(PADDLE_MODEL_DIR),
            "Det.model_path": str(det),
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE,
            # Bound detector work on small plate crops; no global image resizing.
            "Det.limit_type": "max",
            "Det.limit_side_len": 736,
            "Rec.model_path": str(rec),
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.lang_type": LangRec.TH if self.language == "th" else LangRec.EN,
            "EngineConfig.onnxruntime.intra_op_num_threads": 1,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })
        # RapidOCR 3.10.0 loads lazily. Load both sessions here so read() timing
        # excludes graph construction, matching the existing readers' warm_up().
        candidate._load_det_model()
        candidate._load_rec_model()
        self._reader = candidate

    def warm_up(self) -> None:
        with self._lock:
            self._load_reader()

    def read(self, image: np.ndarray, bbox: BBox | None) -> OCRResult:
        started = perf_counter()
        try:
            with self._lock:
                self._load_reader()
                result = self._reader(image, use_det=True, use_cls=False, use_rec=True)
            texts, scores = [], []
            for raw, score in zip(result.txts or (), result.scores or ()):
                text = normalize_plate_text(raw)
                if text:
                    score = float(score)
                    if not 0 <= score <= 1:
                        raise ValueError("PaddleOCR confidence is outside 0..1")
                    texts.append(text)
                    scores.append(score)
            text = normalize_plate_text("".join(texts))
            confidence = min(scores) if scores else None
            status = "success" if text and confidence is not None and confidence >= self.min_confidence else "needs_review"
            return OCRResult(
                text=text, confidence=confidence, bbox=bbox, engine=self.engine, status=status,
                processing_ms=(perf_counter() - started) * 1000,
                error=None if status == "success" else "PaddleOCR found no readable text or confidence is below the review threshold",
            )
        except Exception as exc:
            return OCRResult(bbox=bbox, engine=self.engine, processing_ms=(perf_counter() - started) * 1000,
                             error=f"PaddleOCR failed: {exc}")
