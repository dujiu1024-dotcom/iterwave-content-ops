import asyncio
from src.adapters.base import ContentToPublish, Platform
from src.adapters.draft_saver import DraftSaver


class _Page:
    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None


def _content():
    return ContentToPublish(
        content_id="content-uncertain",
        platform=Platform.XHS,
        title="测试标题",
        body="测试正文",
        image_paths=[],
        tags=[],
    )


def _prepare_pre_save_path(monkeypatch):
    import src.adapters.draft_saver as module

    async def no_op(*_args, **_kwargs):
        return None

    async def not_logged_out(*_args, **_kwargs):
        return False

    async def no_signal(*_args, **_kwargs):
        return None

    async def fill(*_args, **_kwargs):
        return True

    async def count(*_args, **_kwargs):
        return 0

    async def click(*_args, **_kwargs):
        return True

    monkeypatch.setattr(module, "_install_publish_network_guard", no_op)
    monkeypatch.setattr(DraftSaver, "_check_login_expired", not_logged_out)
    monkeypatch.setattr(DraftSaver, "_detect_cb_signal", no_signal)
    monkeypatch.setattr(DraftSaver, "_ensure_image_tab", no_op)
    monkeypatch.setattr(DraftSaver, "fill_first_visible", fill)
    monkeypatch.setattr(DraftSaver, "_draft_count", count)
    monkeypatch.setattr(DraftSaver, "_click_save_draft_button", click)


def test_exception_before_save_attempt_remains_explicit_failure(monkeypatch):
    _prepare_pre_save_path(monkeypatch)

    async def fail_login_check(*_args, **_kwargs):
        raise RuntimeError("login page inspection failed")

    monkeypatch.setattr(DraftSaver, "_check_login_expired", fail_login_check)
    result = asyncio.run(DraftSaver(Platform.XHS).save_draft_via_page(_Page(), _content()))

    assert result.success is False
    assert result.needs_human_verify is False


def test_exception_during_save_click_is_marked_for_human_verification(monkeypatch):
    _prepare_pre_save_path(monkeypatch)

    async def click_then_raise(*_args, **_kwargs):
        # Browser action may have reached the platform even though Playwright reports an error.
        raise RuntimeError("click acknowledgement timed out")

    monkeypatch.setattr(DraftSaver, "_click_save_draft_button", click_then_raise)
    result = asyncio.run(DraftSaver(Platform.XHS).save_draft_via_page(_Page(), _content()))

    assert result.success is False
    assert result.needs_human_verify is True
    assert result.platform_draft_id_missing is True
    assert "人工核实" in result.error_msg


def test_exception_after_save_click_is_marked_for_human_verification(monkeypatch):
    _prepare_pre_save_path(monkeypatch)

    async def verification_failed(*_args, **_kwargs):
        raise RuntimeError("platform confirmation probe crashed")

    monkeypatch.setattr(DraftSaver, "_wait_save_failure", verification_failed)
    result = asyncio.run(DraftSaver(Platform.XHS).save_draft_via_page(_Page(), _content()))

    assert result.success is False
    assert result.needs_human_verify is True
    assert result.platform_draft_id_missing is True


def _prepare_wechat_path(monkeypatch):
    async def not_logged_out(*_args, **_kwargs):
        return False

    async def no_signal(*_args, **_kwargs):
        return None

    async def composer_open(*_args, **_kwargs):
        return True

    async def frame_ready(*_args, **_kwargs):
        return object()

    async def images_ready(*_args, **_kwargs):
        return None

    async def title_ready(*_args, **_kwargs):
        return None

    async def body_ready(*_args, **_kwargs):
        return True

    monkeypatch.setattr(DraftSaver, "_check_login_expired", not_logged_out)
    monkeypatch.setattr(DraftSaver, "_detect_cb_signal", no_signal)
    monkeypatch.setattr(DraftSaver, "_open_wechat_composer", composer_open)
    monkeypatch.setattr(DraftSaver, "_wechat_composer_frame", frame_ready)
    monkeypatch.setattr(DraftSaver, "_wechat_inject_images", images_ready)
    monkeypatch.setattr(DraftSaver, "_wechat_fill_title", title_ready)
    monkeypatch.setattr(DraftSaver, "_wechat_fill_body", body_ready)


def test_wechat_save_click_exception_is_marked_for_human_verification(monkeypatch):
    _prepare_wechat_path(monkeypatch)

    async def click_then_raise(*_args, **_kwargs):
        raise RuntimeError("frame click acknowledgement timed out")

    monkeypatch.setattr(DraftSaver, "_wechat_click_save", click_then_raise)
    content = ContentToPublish(
        content_id="wechat-uncertain",
        platform=Platform.WECHAT_CHANNELS,
        title="视频号图文标题",
        body="视频号图文正文",
        image_paths=["image.png"],
    )
    result = asyncio.run(DraftSaver(Platform.WECHAT_CHANNELS)._save_wechat_image_draft(_Page(), content))

    assert result.success is False
    assert result.needs_human_verify is True


def test_wechat_post_click_verification_exception_is_marked_for_human_verification(monkeypatch):
    _prepare_wechat_path(monkeypatch)

    async def clicked(*_args, **_kwargs):
        return True

    async def verification_failed(*_args, **_kwargs):
        raise RuntimeError("draft box frame was destroyed")

    monkeypatch.setattr(DraftSaver, "_wechat_click_save", clicked)
    monkeypatch.setattr(DraftSaver, "_verify_wechat_draft", verification_failed)
    content = ContentToPublish(
        content_id="wechat-uncertain-after-click",
        platform=Platform.WECHAT_CHANNELS,
        title="视频号图文标题",
        body="视频号图文正文",
        image_paths=["image.png"],
    )
    result = asyncio.run(DraftSaver(Platform.WECHAT_CHANNELS)._save_wechat_image_draft(_Page(), content))

    assert result.success is False
    assert result.needs_human_verify is True
