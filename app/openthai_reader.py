from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

import cv2
import numpy as np

from .ocr_reader import normalize_plate_text
from .schemas import BBox, OCRResult

MODEL_REVISION = "0fca92b87621f063f22c8de4d91460fb79f0e207"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "models/openthai-ocr-text-v2.0.0"


class OpenThaiOCRReader:
    """Pinned OpenThai OCR Text 2.0.0, using ONNX Runtime on CPU."""

    engine = "openthai-2.0.0"

    def __init__(self, reader: Any = None, model_dir: Path | None = None) -> None:
        self._reader = reader
        self.model_dir = Path(model_dir or os.getenv("OPENTHAI_MODEL_DIR", str(DEFAULT_MODEL_DIR)))
        self.min_confidence = float(os.getenv("OPENTHAI_MIN_CONFIDENCE", "0.60"))
        self._lock = Lock()

    def _load_reader(self) -> None:
        if self._reader is not None:
            return
        required = ["det.onnx", "det.yml", "rec.onnx", "rec_dict.txt", "config.json", "runtime/line_engine.py"]
        if any(not (self.model_dir / name).is_file() for name in required):
            raise RuntimeError("Run python scripts/setup_openthai.py to download OpenThai OCR Text 2.0.0")
        name = "_parking_openthai_v2"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                name, self.model_dir / "runtime/__init__.py",
                submodule_search_locations=[str(self.model_dir / "runtime")],
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not load the OpenThai runtime")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        from importlib import import_module

        LineOCR = import_module(f"{name}.line_engine").LineOCR
        # The upstream constructor changes OpenCV's process-wide thread count.
        # Restore it so loading this optional engine does not change other work.
        previous_threads = cv2.getNumThreads()
        try:
            self._reader = LineOCR(
                self.model_dir, threads=1, runtime={"backend": "onnxruntime", "precision": "fp32"},
            )
        finally:
            cv2.setNumThreads(previous_threads)

    def warm_up(self) -> None:
        with self._lock:
            self._load_reader()

    def read(self, image: np.ndarray, bbox: BBox | None) -> OCRResult:
        started = perf_counter()
        try:
            with self._lock:
                self._load_reader()
                payload = self._reader.read(image)
            text = normalize_plate_text(payload.get("text", ""))
            confidences = [float(line["confidence"]) for line in payload.get("lines", []) if line.get("text")]
            if any(not 0 <= value <= 1 for value in confidences):
                raise ValueError("OpenThai confidence is outside 0..1")
            confidence = min(confidences) if confidences else None
            status = "success" if text and confidence is not None and confidence >= self.min_confidence else "needs_review"
            return OCRResult(
                text=text, confidence=confidence, bbox=bbox, engine="openthai-2.0.0",
                processing_ms=(perf_counter() - started) * 1000, status=status,
                error=None if status == "success" else "OpenThai found no readable text or confidence is below the review threshold",
            )
        except Exception as exc:
            return OCRResult(
                bbox=bbox, engine="openthai-2.0.0", processing_ms=(perf_counter() - started) * 1000,
                error=f"OpenThai failed: {exc}",
            )
