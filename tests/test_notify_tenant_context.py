from __future__ import annotations

from types import SimpleNamespace

from src.core.notify import Notifier


def test_notifier_prefixes_tenant_name_in_delivery_title(monkeypatch):
    cfg = SimpleNamespace(notifications=SimpleNamespace(
        channels={
            "macos": SimpleNamespace(enabled=False),
            "wecom": SimpleNamespace(enabled=True, webhook_url_env="TEST_WEBHOOK"),
            "feishu": SimpleNamespace(enabled=False),
        },
        events=[],
    ))
    notifier = Notifier(cfg, object(), tenant_name="华东运营组")
    delivered = {}
    monkeypatch.setattr(notifier, "_rule_override", lambda _event: None)
    monkeypatch.setattr(notifier, "_is_duplicate", lambda *_args: False)
    def send_webhook(_channel, _url_env, title, _body):
        delivered["title"] = title
        return True

    monkeypatch.setattr(notifier, "_send_webhook", send_webhook)
    monkeypatch.setattr(notifier, "_save", lambda *_args, **_kwargs: None)

    assert notifier.emit("draft_failed", "小红书草稿保存失败")
    assert delivered["title"].startswith("华东运营组 · ")
