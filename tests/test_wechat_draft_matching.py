"""Regression tests for strict WeChat draft identity matching."""

import asyncio

from src.adapters.base import Platform
from src.adapters.draft_saver import DraftSaver, _count_exact_title_lines


def test_wechat_draft_readback_requires_one_exact_full_title_line():
    title = "ＡＩ  教育：入门指南"

    assert _count_exact_title_lines("草稿箱\nAI 教育：入门指南\n刚刚保存", title) == 1
    assert _count_exact_title_lines("草稿箱\nAI 教育：入门指南（副本）", title) == 0
    assert _count_exact_title_lines("草稿箱\nAI 教育\n入门指南", title) == 0


def test_wechat_draft_readback_rejects_duplicate_exact_title_lines():
    title = "同一份草稿标题"
    text = f"草稿箱\n{title}\n其他内容\n{title}"

    assert _count_exact_title_lines(text, title) == 2


class _Frame:
    def __init__(self, value):
        self.value = value

    async def evaluate(self, *_args, **_kwargs):
        return self.value


def test_wechat_title_fill_rejects_truncated_readback():
    saver = DraftSaver(Platform.WECHAT_CHANNELS)

    result = asyncio.run(saver._wechat_fill_title(
        _Frame("这是一段被截断的标题"),
        "这是一段被截断的标题但实际期望还有后半段",
    ))

    assert result is not None
    assert "标题回读不一致" in result


def test_wechat_title_fill_accepts_exact_readback():
    saver = DraftSaver(Platform.WECHAT_CHANNELS)
    title = "完整标题回读验证"

    result = asyncio.run(saver._wechat_fill_title(_Frame(title), title))

    assert result is None
