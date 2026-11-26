"""Download checksummed PP-OCRv5 ONNX models from RapidAI's pinned release."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.paddleocr_reader import PADDLE_MODEL_DIR

MODELS = {
    "det/ch_PP-OCRv5_det_mobile.onnx": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    "rec/th_PP-OCRv5_rec_mobile.onnx": "de541dd83161c241ff426f7ecfd602a0ba77d686cf3ab9a6c255ea82fd08006e",
    "rec/en_PP-OCRv5_rec_mobile.onnx": "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8",
}


def main() -> None:
    base = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/"
    PADDLE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=90) as client:
        for remote, expected in MODELS.items():
            target = PADDLE_MODEL_DIR / Path(remote).name
            content = target.read_bytes() if target.is_file() else b""
            if hashlib.sha256(content).hexdigest() != expected:
                response = client.get(base + remote)
                response.raise_for_status()
                content = response.content
            if hashlib.sha256(content).hexdigest() != expected:
                raise RuntimeError(f"Checksum mismatch: {remote}")
            target.write_bytes(content)
            print(f"Verified {target.name}: {len(content)} bytes", flush=True)
    (PADDLE_MODEL_DIR / "manifest.json").write_text(json.dumps({"release": "RapidOCR v3.10.0", "base": base, "sha256": MODELS}, indent=2) + "\n")


if __name__ == "__main__":
    main()
