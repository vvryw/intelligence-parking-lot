import numpy as np

from app.plate_pipeline import PlatePipeline, crop_manual


def test_manual_crop_uses_original_image_coordinates_and_clips():
    image = np.zeros((50, 100, 3), dtype=np.uint8)
    result = crop_manual(image, (-5, 10, 120, 40))
    assert result.bbox == (0, 10, 100, 40)
    assert result.image.shape == (30, 100, 3)


def test_invalid_manual_crop_is_explicit():
    result = PlatePipeline().extract(
        np.zeros((50, 100, 3), dtype=np.uint8), (20, 20, 20, 40)
    )
    assert result.image is None
    assert result.error

