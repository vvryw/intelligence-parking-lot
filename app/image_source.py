from __future__ import annotations

import os
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


def save_image(image: np.ndarray, path: str | Path) -> Path:
    destination = Path(path).expanduser().resolve()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(destination), image):
            raise ImageSourceError(f"Could not save image to {destination}")
    except (OSError, cv2.error) as exc:
        raise ImageSourceError(f"Could not save image to {destination}: {exc}") from exc
    return destination


def fetch_camera_image(url: str, timeout_seconds: float = 10.0) -> tuple[np.ndarray, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageSourceError("Camera URL must be an absolute HTTP(S) URL")
    try:
        import httpx
    except ImportError as exc:
        raise ImageSourceError("httpx is not installed; install requirements.txt") from exc
    try:
        response = httpx.get(url, timeout=timeout_seconds, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ImageSourceError(f"Camera capture failed: {exc}") from exc
    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.lower().startswith("image/"):
        raise ImageSourceError(f"Camera returned non-image content type: {content_type}")
    # Entrance needs a 180-degree rotation relative to the shared horizontal
    # correction: H followed by H+V is equivalent to V on the sensor image.
    # Apply this before previews and OCR so plate boxes use the same pixels.
    entrance_url = os.getenv("ESP32_ENTRANCE_CAPTURE_URL", "").strip() or os.getenv("ESP32_CAPTURE_URL", "").strip()
    flip_code = 0 if entrance_url and url == entrance_url else 1
    image = cv2.flip(decode_image(response.content), flip_code)
    return image, "esp32_capture.jpg"

