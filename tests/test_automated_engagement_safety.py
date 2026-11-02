import asyncio

import pytest

from src.core.ops.engine import OperationsEngine


def _engine_with_stub_adapter():
    engine = OperationsEngine.__new__(OperationsEngine)
    platform = object()
    engine.adapters = {platform: object()}
    logged = []
    engine._log_op = lambda *args, **kwargs: logged.append((args, kwargs))
    return engine, platform, logged


def test_automated_engagement_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ITERWAVE_ENABLE_AUTOMATED_ENGAGEMENT", raising=False)
    engine, platform, logged = _engine_with_stub_adapter()

    assert asyncio.run(engine.passive_reply(platform, "draft-1", "comment-1", "reply")) is False
    assert asyncio.run(engine.active_comment(platform, "https://example.test/post", "comment")) is None
    assert asyncio.run(engine.follow(platform, "user-1")) is False

    assert [item[0][1] for item in logged] == [
        "passive_reply", "active_comment", "follow",
    ]
    assert all(item[0][2] == "skipped" for item in logged)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_automated_engagement_requires_explicit_opt_in(monkeypatch, value):
    monkeypatch.setenv("ITERWAVE_ENABLE_AUTOMATED_ENGAGEMENT", value)
    engine, _, _ = _engine_with_stub_adapter()
    assert engine._automated_engagement_enabled() is True
