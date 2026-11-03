"""视觉重出图/单页重做会调用外部模型与 Chromium（数十秒），不得被请求级事务包住持锁。"""
from src.api import main as api_main


def _route(app, path, method="POST"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {path}")


def test_marker_skips_request_level_transaction():
    calls = []

    @api_main.manages_own_transaction
    def endpoint():
        calls.append("own")
        return {"ok": True}

    route = api_main.TransactionalAPIRoute("/api/x", endpoint, methods=["POST"])
    # Keep cleanup wrapper while skipping the request-level transaction.
    assert route.endpoint is not endpoint
    assert getattr(route.endpoint, "_manages_own_transaction", False)
    assert route.endpoint() == {"ok": True}
    assert calls == ["own"]


def test_unmarked_sync_write_route_is_wrapped():
    def endpoint():
        return {"ok": True}

    route = api_main.TransactionalAPIRoute("/api/y", endpoint, methods=["POST"])
    assert route.endpoint is not endpoint


def test_visual_long_running_routes_are_marked():
    src = open(api_main.__file__, encoding="utf-8").read()
    for name in ("def revise_visual_version_page(", "def regenerate_cards("):
        idx = src.index(name)
        assert "@manages_own_transaction" in src[max(0, idx - 200):idx], name
