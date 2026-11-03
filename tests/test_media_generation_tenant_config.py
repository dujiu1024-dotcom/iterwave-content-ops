from types import SimpleNamespace

import pytest

from src.core.media_generation import ImageGenerationError, generate_image_bytes


def test_generate_image_bytes_prefers_bound_tenant_config(monkeypatch):
    monkeypatch.setattr(
        "src.core.visual_ocr.preflight_ocr",
        lambda **_kwargs: {"status": "available"},
    )
    tenant_cfg = SimpleNamespace(
        media_generation=SimpleNamespace(base_url="https://tenant-image.example")
    )

    monkeypatch.setattr(
        "src.core.task_context.current_config", lambda: tenant_cfg
    )

    def unexpected_global_config():
        raise AssertionError("global config must not be read in a tenant task")

    monkeypatch.setattr("src.core.config.get_config", unexpected_global_config)
    monkeypatch.setenv("ITERWAVE_IMAGE_API_KEY_ENV", "")

    with pytest.raises(ImageGenerationError, match="未配置图片生成服务密钥"):
        generate_image_bytes("test", model="test-model", size="1024x1024")


def test_explicit_config_takes_precedence_over_bound_tenant_config(monkeypatch):
    monkeypatch.setattr(
        "src.core.visual_ocr.preflight_ocr",
        lambda **_kwargs: {"status": "available"},
    )
    explicit_cfg = SimpleNamespace(
        media_generation=SimpleNamespace(base_url="https://explicit-image.example")
    )
    bound_cfg = SimpleNamespace(
        media_generation=SimpleNamespace(base_url="https://bound-image.example")
    )
    monkeypatch.setattr("src.core.task_context.current_config", lambda: bound_cfg)
    monkeypatch.setattr("src.core.config.get_config", lambda: pytest.fail("global config fallback"))

    monkeypatch.setenv("ITERWAVE_IMAGE_API_KEY_ENV", "")
    with pytest.raises(ImageGenerationError, match="未配置图片生成服务密钥"):
        generate_image_bytes(
            "test", model="test-model", size="1024x1024", cfg=explicit_cfg
        )


@pytest.mark.parametrize(
    "reason",
    ["tesseract_not_installed", "ocr_language_missing", "tesseract_not_executable", "ocr_timeout_invalid"],
)
def test_missing_ocr_blocks_provider_request_before_any_api_call(monkeypatch, reason):
    from src.core import media_generation

    monkeypatch.setattr(
        "src.core.visual_ocr.preflight_ocr",
        lambda **_kwargs: {"status": "unavailable", "reason": reason},
    )
    monkeypatch.setenv("ITERWAVE_IMAGE_API_KEY_ENV", "IMAGE_TEST_KEY")
    monkeypatch.setenv("IMAGE_TEST_KEY", "test-key")
    monkeypatch.setattr(
        media_generation, "_request_endpoint",
        lambda *args, **kwargs: pytest.fail("image API must not be called"),
    )
    cfg = SimpleNamespace(media_generation=SimpleNamespace(
        base_url="https://image.example", model="test-model",
        api_key_env="IMAGE_TEST_KEY",
    ))
    with pytest.raises(ImageGenerationError, match="OCR 前置预检失败"):
        generate_image_bytes("test prompt", model="test-model", size="1024x1024", cfg=cfg)
