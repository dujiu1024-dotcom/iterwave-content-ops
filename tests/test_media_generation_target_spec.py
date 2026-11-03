import pytest

from src.core.media_generation import ImageGenerationError, validate_target_image_spec


class _Quality:
    portrait_ratio_min = 0.55
    portrait_ratio_max = 0.85
    square_ratio_max = 1.1
    landscape_ratio_max = 1.7


class _Cfg:
    media_quality = _Quality()


def test_target_image_spec_accepts_requested_portrait_dimensions_with_small_error():
    validate_target_image_spec(1030, 1530, "1024x1536", cfg=_Cfg())


def test_target_image_spec_rejects_wrong_width_or_height():
    with pytest.raises(ImageGenerationError, match="尺寸不符合"):
        validate_target_image_spec(1100, 1536, "1024x1536", cfg=_Cfg())
    with pytest.raises(ImageGenerationError, match="尺寸不符合"):
        validate_target_image_spec(1024, 1400, "1024x1536", cfg=_Cfg())


def test_target_image_spec_rejects_wrong_aspect_ratio_and_portrait_policy():
    with pytest.raises(ImageGenerationError, match="纵横比"):
        validate_target_image_spec(1054, 1490, "1024x1536", cfg=_Cfg())


def test_target_image_spec_requires_explicit_target_dimensions():
    with pytest.raises(ImageGenerationError, match="WIDTHxHEIGHT"):
        validate_target_image_spec(1024, 1536, "portrait", cfg=_Cfg())
