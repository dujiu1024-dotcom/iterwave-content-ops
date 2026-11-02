import asyncio

from src.adapters.draft_saver import DraftSaver


class _Button:
    def __init__(self, label):
        self.label = label
        self.clicked = False

    @property
    def first(self):
        return self

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


def _saver():
    saver = DraftSaver.__new__(DraftSaver)
    saver.platform = "xhs"
    return saver


def test_draft_saver_rejects_publish_button_even_if_selector_matches():
    page = _Page("立即发布")
    result = asyncio.run(_saver()._click_save_draft_button(page, ["button.primary"]))
    assert result is False
    assert page.button.clicked is False


def test_draft_saver_fails_closed_on_ambiguous_button_label():
    page = _Page("保存")
    result = asyncio.run(_saver()._click_save_draft_button(page, ["button.save-draft"]))
    assert result is False
    assert page.button.clicked is False


def test_draft_saver_accepts_explicit_draft_save_label():
    page = _Page("暂存离开")
    result = asyncio.run(_saver()._click_save_draft_button(page, ["button.save-draft"]))
    assert result is True
    assert page.button.clicked is True
