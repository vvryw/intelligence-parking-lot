from __future__ import annotations

import os
from typing import Literal

CameraRole = Literal["entrance", "exit"]
CAMERA_LABELS = {"entrance": "Entrance · Coming in", "exit": "Exit · Going out"}


def camera_url(role: CameraRole = "entrance") -> str:
    url = os.getenv(f"ESP32_{role.upper()}_CAPTURE_URL", "").strip()
    if not url and role == "entrance":
        url = os.getenv("ESP32_CAPTURE_URL", "").strip()
    if not url:
        raise ValueError(f"{CAMERA_LABELS[role]} camera is not configured yet.")
    return url
