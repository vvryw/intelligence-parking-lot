from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .schemas import BBox


@dataclass(slots=True)
class PlateCrop:
    image: np.ndarray | None
    bbox: BBox | None
    method: str
    error: str | None = None


def _clip_bbox(bbox: BBox, width: int, height: int) -> BBox | None:
    x1, y1, x2, y2 = bbox
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def crop_manual(image: np.ndarray, bbox: BBox) -> PlateCrop:
    height, width = image.shape[:2]
    clipped = _clip_bbox(bbox, width, height)
    if clipped is None:
        return PlateCrop(None, None, "manual", "Manual plate bbox is empty or outside the image")
    x1, y1, x2, y2 = clipped
    return PlateCrop(image[y1:y2, x1:x2].copy(), clipped, "manual")


def locate_plate_heuristic(image: np.ndarray) -> PlateCrop:
    """Find a plate-like rectangle; this is a baseline, not a trained plate detector."""
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    edges = cv2.Canny(gray, 50, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    image_area = float(width * height)
    candidates: list[tuple[float, BBox]] = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:100]:
        x, y, w, h = cv2.boundingRect(contour)
        if h == 0:
            continue
        ratio = w / h
        relative_area = (w * h) / image_area
        if not (1.5 <= ratio <= 6.5 and 0.002 <= relative_area <= 0.20):
            continue
        perimeter = cv2.arcLength(contour, True)
        corners = len(cv2.approxPolyDP(contour, 0.03 * perimeter, True))
        rectangularity = min(cv2.contourArea(contour) / max(w * h, 1), 1.0)
        score = rectangularity + (0.25 if 4 <= corners <= 6 else 0.0) + relative_area
        candidates.append((score, (x, y, x + w, y + h)))

    if not candidates:
        return PlateCrop(
            None,
            None,
            "heuristic",
            "No plate-like region found; provide --plate-bbox x1,y1,x2,y2 for manual crop",
        )
    _, bbox = max(candidates, key=lambda item: item[0])
    x1, y1, x2, y2 = bbox
    return PlateCrop(image[y1:y2, x1:x2].copy(), bbox, "heuristic")


def preprocess_plate(crop: np.ndarray) -> np.ndarray:
    height, width = crop.shape[:2]
    scale = min(max(1.0, 480.0 / width), 960.0 / max(width, height))
    resized = cv2.resize(
        cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_CUBIC if scale >= 1 else cv2.INTER_AREA,
    )
    denoised = cv2.fastNlMeansDenoising(resized, None, 10, 7, 21)
    contrasted = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(denoised)
    softened = cv2.GaussianBlur(contrasted, (0, 0), sigmaX=0.9)
    sharpened = cv2.addWeighted(contrasted, 1.25, softened, -0.25, 0)
    # Widen the contrast around the midtones while preserving gray character edges.
    return cv2.addWeighted(sharpened, 1.23, sharpened, 0.0, -24.0)


class PlatePipeline:
    def extract(self, image: np.ndarray, manual_bbox: BBox | None = None) -> PlateCrop:
        crop = crop_manual(image, manual_bbox) if manual_bbox else locate_plate_heuristic(image)
        if crop.image is not None:
            crop.image = preprocess_plate(crop.image)
        return crop

