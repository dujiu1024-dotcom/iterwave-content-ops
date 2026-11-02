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



def test_publish_network_guard_blocks_publish_endpoint_and_action_payload():
    from src.adapters.draft_saver import _is_publish_network_request

    assert _is_publish_network_request("POST", "https://creator.example/api/publish/submit")
    assert _is_publish_network_request(
        "POST", "https://creator.example/api/content",
        '{"operation":"publish","content_id":"123"}',
    )


def test_publish_network_guard_allows_draft_save_upload_and_navigation():
    from src.adapters.draft_saver import _is_publish_network_request

    assert not _is_publish_network_request(
        "POST", "https://creator.example/api/publish/draft/save",
    )
    assert not _is_publish_network_request(
        "POST", "https://creator.example/api/upload/image",
    )
    assert not _is_publish_network_request(
        "GET", "https://creator.example/publish/editor",
    )


def test_publish_network_guard_does_not_treat_arbitrary_text_as_action():
    from src.adapters.draft_saver import _is_publish_network_request

    assert not _is_publish_network_request(
        "POST", "https://creator.example/api/content",
        '{"title":"how to publish content","body":"publish is a common word"}',
    )



def test_publish_network_guard_blocks_publish_action_even_on_draft_path():
    from src.adapters.draft_saver import _is_publish_network_request

    assert _is_publish_network_request(
        "POST", "https://creator.example/api/draft/save",
        '{"action":"publish"}',
    )


def test_publish_network_guard_blocks_form_encoded_publish_action():
    from src.adapters.draft_saver import _is_publish_network_request

    assert _is_publish_network_request(
        "POST", "https://creator.example/api/content",
        "operation=publish&content_id=123",
    )



def test_publish_network_route_handler_aborts_publish_and_continues_draft_save():
    from src.adapters.draft_saver import _install_publish_network_guard

    class Page:
        def __init__(self):
            self.handler = None
            self.routes = 0

        async def route(self, _pattern, handler):
            self.handler = handler
            self.routes += 1

    class Request:
        method = "POST"
        url = "https://creator.example/api/publish/submit"
        post_data = '{"action":"publish"}'

    class Route:
        request = Request()

        def __init__(self):
            self.aborted = None
            self.continued = False

        async def abort(self, error_code=None):
            self.aborted = error_code

        async def continue_(self):
            self.continued = True

    async def run():
        page = Page()
        await _install_publish_network_guard(page)
        await _install_publish_network_guard(page)
        assert page.routes == 1
        publish_route = Route()
        await page.handler(publish_route)
        assert publish_route.aborted == "blockedbyclient"
        assert not publish_route.continued

        draft_route = Route()
        draft_route.request = type("DraftRequest", (), {
            "method": "POST",
            "url": "https://creator.example/api/drafts/save",
            "post_data": '{"title":"draft"}',
        })()
        await page.handler(draft_route)
        assert draft_route.continued
        assert draft_route.aborted is None

    asyncio.run(run())


def test_publish_network_guard_fails_closed_when_route_installation_fails():
    from src.adapters.draft_saver import _install_publish_network_guard

    class BrokenPage:
        async def route(self, *_args, **_kwargs):
            raise RuntimeError("route unavailable")

    async def run():
        with pytest.raises(RuntimeError, match="安全拦截未能启用"):
            await _install_publish_network_guard(BrokenPage())

    import pytest
    asyncio.run(run())



def test_publish_network_guard_blocks_query_and_camel_case_publish_actions():
    from src.adapters.draft_saver import _is_publish_network_request

    assert _is_publish_network_request(
        "POST", "https://creator.example/api/draft/save?action=publish",
    )
    assert _is_publish_network_request(
        "POST", "https://creator.example/api/content",
        '{"publishNow":true,"metadata":{"submitType":"submit-now"}}',
    )
    assert _is_publish_network_request(
        "POST", "https://creator.example/api/content?publish_action=publish-now",
    )



def test_publish_network_guard_checks_all_duplicate_action_parameters():
    from src.adapters.draft_saver import _is_publish_network_request

    assert _is_publish_network_request(
        "POST", "https://creator.example/api/drafts/save?action=publish&action=draft",
    )
    assert _is_publish_network_request(
        "POST", "https://creator.example/api/content",
        "action=publish&action=draft",
    )


def test_publish_network_guard_is_installed_once_per_browser_context():
    from src.adapters.draft_saver import _install_publish_network_guard

    class Context:
        def __init__(self):
            self.pages = []
            self.handler = None
            self.routes = 0

        async def route(self, _pattern, handler):
            self.handler = handler
            self.routes += 1

    class Page:
        def __init__(self, context):
            self.context = context

        async def route(self, *_args, **_kwargs):
            raise AssertionError("page-only route must not be used when a context exists")

    async def run():
        context = Context()
        await _install_publish_network_guard(Page(context))
        await _install_publish_network_guard(Page(context))
        assert context.routes == 1
        assert context.handler is not None

    asyncio.run(run())
