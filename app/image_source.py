from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np


class ImageSourceError(ValueError):
    pass


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise ImageSourceError("Image payload is empty")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageSourceError("Payload is not a supported image")
    return image


def load_local_image(path: str | Path) -> tuple[np.ndarray, str]:
    source = Path(path)
    if not source.is_file():
        raise ImageSourceError(f"Image does not exist: {source}")
    return decode_image(source.read_bytes()), source.name


def fetch_camera_image(url: str, timeout_seconds: float = 10.0) -> tuple[np.ndarray, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageSourceError("Camera URL must be an absolute HTTP(S) URL")
    try:
        try:
            import httpx
        except ImportError as exc:
            raise ImageSourceError("httpx is not installed; install requirements.txt") from exc
        response = httpx.get(url, timeout=timeout_seconds, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ImageSourceError(f"Camera capture failed: {exc}") from exc
    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.lower().startswith("image/"):
        raise ImageSourceError(f"Camera returned non-image content type: {content_type}")
    return decode_image(response.content), "esp32_capture.jpg"

