"""Regression tests for platform readback outcome classification."""

import pytest

from src.vendor.easel import platform_readback as readback


@pytest.mark.parametrize(
    ("reader_name", "verify_name", "reader_args"),
    [
        ("read_douyin_works", "verify_douyin_publish", (object(),)),
        ("read_kuaishou_works", "verify_kuaishou_publish", (object(),)),
        ("read_bilibili_works", "verify_bilibili_publish", ("cookies.json",)),
    ],
)
def test_successful_empty_readback_after_transient_error_is_unverified(
    monkeypatch, reader_name, verify_name, reader_args
):
    """A successful empty response proves the channel works, even after an earlier error."""
    calls = iter([RuntimeError("temporary network failure"), []])

    def fake_reader(*_args, **_kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(readback, reader_name, fake_reader)
    monkeypatch.setattr(readback.time, "sleep", lambda *_args: None)
    verify = getattr(readback, verify_name)

    result = verify(*reader_args, title="本次发布标题", attempts=2, delay_s=0)

    assert result.outcome == "unverified"
    assert result.error is None
    assert result.evidence["attempts"] == 2
    assert result.evidence["count"] == 0
    assert result.evidence["recovered_after_readback_error"] is True


@pytest.mark.parametrize(
    ("reader_name", "verify_name", "reader_args"),
    [
        ("read_douyin_works", "verify_douyin_publish", (object(),)),
        ("read_kuaishou_works", "verify_kuaishou_publish", (object(),)),
        ("read_bilibili_works", "verify_bilibili_publish", ("cookies.json",)),
    ],
)
def test_all_readback_attempts_failing_is_readback_error(
    monkeypatch, reader_name, verify_name, reader_args
):
    def fail_reader(*_args, **_kwargs):
        raise RuntimeError("readback unavailable")

    monkeypatch.setattr(readback, reader_name, fail_reader)
    monkeypatch.setattr(readback.time, "sleep", lambda *_args: None)
    verify = getattr(readback, verify_name)

    result = verify(*reader_args, title="本次发布标题", attempts=2, delay_s=0)

    assert result.outcome == "readback_error"
    assert "readback unavailable" in result.error

