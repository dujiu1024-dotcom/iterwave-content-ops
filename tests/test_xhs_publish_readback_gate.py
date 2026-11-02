"""Fail-closed behavior for XHS publishing without authoritative readback."""

import pytest

from src.vendor.easel import xhs_publish


def test_xhs_ui_success_without_readback_is_not_reported_as_published():
    with pytest.raises(SystemExit) as caught:
        xhs_publish._publish_result_without_readback()
    assert caught.value.code == 5


def test_xhs_ui_only_success_requires_explicit_opt_in(capsys):
    result = xhs_publish._publish_result_without_readback(allow_ui_only_success=True)
    assert result == "unverified"
    assert "未取得平台作品读回证据" in capsys.readouterr().err
