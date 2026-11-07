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


def test_error_notifications_hide_tracebacks_and_database_paths(monkeypatch):
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
    monkeypatch.setattr(notifier, "_send_webhook", lambda _channel, _url_env, _title, body: delivered.setdefault("body", body) or True)
    monkeypatch.setattr(notifier, "_save", lambda *_args, **_kwargs: None)

    assert notifier.emit(
        "draft_failed",
        "小红书存草稿失败：示例标题 — sqlite3.OperationalError: unable to open "
        "/home/ubuntu/iterwave/data/private.db\\nTraceback (most recent call last):",
    )
    assert delivered["body"] == "任务执行失败，详细诊断已隐藏。"
    assert "/home/ubuntu" not in delivered["body"]
    assert "sqlite3.OperationalError" not in delivered["body"]
    assert "Traceback" not in delivered["body"]


def test_error_notifications_keep_normal_business_context_and_hide_paths(monkeypatch):
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
    monkeypatch.setattr(notifier, "_send_webhook", lambda _channel, _url_env, _title, body: delivered.setdefault("body", body) or True)
    monkeypatch.setattr(notifier, "_save", lambda *_args, **_kwargs: None)

    assert notifier.emit(
        "data_fetch_failed",
        "小红书数据拉取未成功：示例标题 — 请求超时，诊断文件 /home/ubuntu/iterwave/logs/fetch.log",
    )
    assert delivered["body"].startswith("小红书数据拉取未成功：示例标题")
    assert "/home/ubuntu" not in delivered["body"]
    assert "服务器内部路径" in delivered["body"]
