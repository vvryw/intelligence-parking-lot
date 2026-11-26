"""Download the exact OpenThai OCR Text 2.0.0 release, without PyTorch training weights."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.openthai_reader import DEFAULT_MODEL_DIR, MODEL_REVISION

FILES = {
    "NOTICE": "4c0e4f27877836de51e29aec3918b6c743c3028cd4163b6661fd540336db32b3",
    "config.json": "11599c0dae206381cb8ea11c1b7ce130b2e7971c19219f409785ee8c02ab5d94",
    "det.onnx": "193bab7a04fca699a6c82e6abb5b81bdb28177f0abd4062552b04908dafb19f8",
    "det.yml": "3ac018be6f97499a08faa3bbdeb33640968d9307f6736d152902747a9f259593",
    "rec.onnx": "82ec89533346faadcb30df96703dad2dd7521de65709e60d83fd9745b8968124",
    "rec_dict.txt": "259ef2636cec0eeae225ae9fd9a63fa9305285650c22d7ec34fe8a82b24c2f1a",
    "runtime/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "runtime/line_engine.py": "5d5d672658d2a82ca6b32e230bb551eefe5d1e4efa46ff21908bfcfd75e5495c",
    "runtime/thai_norm.py": "53a03ac139fb0de3e7aec0f5b8ab69b0b519fb5e636f7c1de09cb357b023344a",
}


def main() -> None:
    base = f"https://huggingface.co/iapp/OpenThai2.1-OCR-Text-20261118/resolve/{MODEL_REVISION}/"
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for name, expected in FILES.items():
            path = DEFAULT_MODEL_DIR / name
            content = path.read_bytes() if path.is_file() else b""
            if hashlib.sha256(content).hexdigest() != expected or not path.is_file():
                response = client.get(base + name)
                response.raise_for_status()
                content = response.content
            if hashlib.sha256(content).hexdigest() != expected:
                raise RuntimeError(f"Checksum mismatch: {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            print(f"Verified {name}", flush=True)
    (DEFAULT_MODEL_DIR / "REVISION").write_text(MODEL_REVISION + "\n")
    print(f"OpenThai OCR Text 2.0.0 ready: {DEFAULT_MODEL_DIR}")


if __name__ == "__main__":
    main()
