from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import sqlite3

from src.adapters.base import Platform
from src.core.content_pipeline import _log_ai_call


def test_ai_call_audit_timestamp_uses_tenant_business_timezone(monkeypatch):
    import src.core.timezone as timezone

    expected = datetime(2026, 11, 2, 12, 34, 56, tzinfo=ZoneInfo("Asia/Singapore"))
    monkeypatch.setattr(timezone, "business_now", lambda _cfg: expected)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE ai_calls (
            step TEXT, module TEXT, model TEXT, prompt_version TEXT,
            input_digest TEXT, tokens_in INTEGER, tokens_out INTEGER,
            latency_ms INTEGER, duration_ms INTEGER, output_valid INTEGER,
            success INTEGER, called_at TEXT
        )
        """
    )

    _log_ai_call(
        SimpleNamespace(business_timezone="Asia/Singapore"),
        conn,
        Platform.XHS,
        "test-model",
        "topic-1",
        7,
        latency_ms=12,
        tokens_in=3,
        tokens_out=4,
        success=True,
        output_valid=True,
    )

    row = conn.execute("SELECT called_at FROM ai_calls").fetchone()
    assert row[0] == "2026-11-02 12:34:56"
    conn.close()
