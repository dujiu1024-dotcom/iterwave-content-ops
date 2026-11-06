"""Regression tests for fail-closed WeChat draft body persistence."""

import asyncio

from src.adapters.base import ContentToPublish, Platform
from src.adapters.draft_saver import DraftSaver


class _Frame:
    def __init__(self, result):
        self.result = result

    async def evaluate(self, *_args, **_kwargs):
        return self.result


def test_wechat_body_fill_requires_exact_readback():
    saver = DraftSaver(Platform.WECHAT_CHANNELS)
    body = "第一段\n第二段  内容"

    assert asyncio.run(saver._wechat_fill_body(
        _Frame({"ok": True, "text": "第一段\n第二段 内容"}), body,
    )) is True
    assert asyncio.run(saver._wechat_fill_body(
        _Frame({"ok": True, "text": "第一段"}), body,
    )) is False
    assert asyncio.run(saver._wechat_fill_body(
        _Frame({"ok": False, "text": ""}), body,
    )) is False
    assert asyncio.run(saver._wechat_fill_body(
        _Frame(None), body,
    )) is False


class _Page:
    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None


def test_wechat_body_readback_failure_stops_before_save(monkeypatch):
    saver = DraftSaver(Platform.WECHAT_CHANNELS)
    clicked = []

    async def no_login_expiry(*_args, **_kwargs):
        return False

    async def no_circuit_breaker(*_args, **_kwargs):
        return None

    async def composer_open(*_args, **_kwargs):
        return True

    async def frame_ready(*_args, **_kwargs):
        return object()

    async def images_ready(*_args, **_kwargs):
        return None

    async def title_ready(*_args, **_kwargs):
        return None

    async def body_not_verified(*_args, **_kwargs):
        return False

    async def save(*_args, **_kwargs):
        clicked.append(True)
        return True

    monkeypatch.setattr(saver, "_check_login_expired", no_login_expiry)
    monkeypatch.setattr(saver, "_detect_cb_signal", no_circuit_breaker)
    monkeypatch.setattr(saver, "_open_wechat_composer", composer_open)
    monkeypatch.setattr(saver, "_wechat_composer_frame", frame_ready)
    monkeypatch.setattr(saver, "_wechat_inject_images", images_ready)
    monkeypatch.setattr(saver, "_wechat_fill_title", title_ready)
    monkeypatch.setattr(saver, "_wechat_fill_body", body_not_verified)
    monkeypatch.setattr(saver, "_wechat_click_save", save)

    content = ContentToPublish(
        content_id="wechat-body-not-verified",
        platform=Platform.WECHAT_CHANNELS,
        title="视频号草稿标题",
        body="正文必须完整写入",
        image_paths=["image.png"],
    )
    result = asyncio.run(saver._save_wechat_image_draft(_Page(), content))

    assert result.success is False
    assert result.needs_human_verify is False
    assert "正文未能完整写入并回读" in result.error_msg
    assert clicked == []
