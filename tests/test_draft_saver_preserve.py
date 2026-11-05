"""Regression tests for preserving existing unsent platform drafts."""

import pytest

from src.adapters.base import Platform
from src.adapters.draft_saver import DraftSaver


class _Locator:
    def __init__(self, visible):
        self.visible = visible
        self.first = self

    async def count(self):
        return 1 if self.visible else 0

    async def is_visible(self):
        return self.visible


class _Page:
    def __init__(self, visible):
        self.visible = visible
        self.seen = []

    def locator(self, selector):
        self.seen.append(selector)
        return _Locator(self.visible)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "expected_selector"),
    [
        (Platform.DOUYIN, "text=你还有上次未发布的图文"),
        (Platform.KUAISHOU, "text=还有上次未发布的图集"),
    ],
)
async def test_stale_draft_prompt_is_detected_without_discarding(platform, expected_selector):
    saver = DraftSaver(platform)
    page = _Page(visible=True)

    assert await saver._has_stale_draft_prompt(page) is True
    assert expected_selector in page.seen
    # The fake locator intentionally has no click() method: detection must be read-only.


@pytest.mark.asyncio
async def test_xhs_has_no_unverified_stale_prompt_selector():
    # XHS currently has no confirmed equivalent prompt selector. It must not
    # fabricate a positive detection; its persistence verification is tracked separately.
    saver = DraftSaver(Platform.XHS)
    page = _Page(visible=True)

    assert await saver._has_stale_draft_prompt(page) is False
    assert page.seen == []


@pytest.mark.asyncio
async def test_missing_stale_draft_prompt_does_not_block_save():
    saver = DraftSaver(Platform.DOUYIN)
    page = _Page(visible=False)

    assert await saver._has_stale_draft_prompt(page) is False


@pytest.mark.asyncio
async def test_xhs_has_no_unverified_stale_prompt_selector():
    # No equivalent XHS prompt has been confirmed; do not fabricate a positive match.
    saver = DraftSaver(Platform.XHS)
    page = _Page(visible=True)

    assert await saver._has_stale_draft_prompt(page) is False
    assert "draft_prompt_preserve" not in saver.selectors
