"""Regression tests for Kuaishou's confirmed cancel-to-save draft action."""

import pytest

from src.adapters.base import Platform
from src.adapters.draft_saver import DraftSaver


class _Button:
    def __init__(self, label):
        self.label = label
        self.clicked = False
        self.first = self

    async def is_visible(self, timeout=None):
        return True

    async def inner_text(self, timeout=None):
        return self.label

    async def get_attribute(self, name):
        return None

    async def click(self, timeout=None):
        self.clicked = True


class _Page:
    def __init__(self, label):
        self.button = _Button(label)

    def locator(self, selector):
        return self.button


@pytest.mark.asyncio
async def test_kuaishou_configured_cancel_button_is_accepted_as_autosave():
    saver = DraftSaver(Platform.KUAISHOU)
    selector = saver.selectors["save_draft_button"][-1]
    page = _Page("取消")

    assert await saver._click_save_draft_button(page, [selector]) is True
    assert page.button.clicked is True


@pytest.mark.asyncio
async def test_cancel_button_is_not_accepted_for_other_platforms():
    saver = DraftSaver(Platform.XHS)
    page = _Page("取消")

    assert await saver._click_save_draft_button(page, ["text=取消"]) is False
    assert page.button.clicked is False


@pytest.mark.asyncio
async def test_kuaishou_cancel_requires_its_configured_selector():
    saver = DraftSaver(Platform.KUAISHOU)
    page = _Page("取消")

    assert await saver._click_save_draft_button(page, ["text=some-other-cancel"]) is False
    assert page.button.clicked is False


@pytest.mark.asyncio
async def test_kuaishou_publish_label_is_still_rejected():
    saver = DraftSaver(Platform.KUAISHOU)
    selector = saver.selectors["save_draft_button"][-1]
    page = _Page("发布")

    assert await saver._click_save_draft_button(page, [selector]) is False
    assert page.button.clicked is False
