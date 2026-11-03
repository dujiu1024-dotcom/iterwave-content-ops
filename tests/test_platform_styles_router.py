from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.adapters.base import Platform
from src.api.platform_styles import build_platform_styles_router


def _client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE platform_styles ("
        "platform TEXT PRIMARY KEY, title_style TEXT, body_style TEXT, tone TEXT, "
        "hashtags TEXT, updated_at TEXT, updated_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE config_changes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, old_value TEXT, "
        "new_value TEXT, changed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO platform_styles VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("xhs", "标题风格", "正文风格", "克制", '["AI"]', None, None),
    )
    conn.commit()
    context = {"conn": conn}
    app = FastAPI()
    app.include_router(build_platform_styles_router(
        lambda: context,
        lambda: datetime(2026, 11, 3, tzinfo=timezone.utc),
        Platform,
    ))
    return TestClient(app), conn


def test_list_platform_styles_parses_hashtags():
    client, conn = _client()
    try:
        response = client.get("/api/platform_styles")
        assert response.status_code == 200
        assert response.json()["items"] == [{
            "platform": "xhs",
            "title_style": "标题风格",
            "body_style": "正文风格",
            "tone": "克制",
            "hashtags": ["AI"],
            "updated_at": None,
            "updated_by": None,
        }]
    finally:
        conn.close()


def test_update_platform_style_writes_values_and_audit_rows():
    client, conn = _client()
    try:
        response = client.patch("/api/platform_styles/xhs", json={
            "title_style": "问题导向",
            "hashtags": ["#AI", "运营", "AI"],
        })
        assert response.status_code == 200
        assert response.json()["changed"] == 2
        row = conn.execute(
            "SELECT title_style, hashtags, updated_by FROM platform_styles WHERE platform='xhs'"
        ).fetchone()
        assert row["title_style"] == "问题导向"
        assert row["hashtags"] == '["AI", "运营"]'
        assert row["updated_by"] == "settings_ui"
        audits = conn.execute(
            "SELECT key FROM config_changes ORDER BY key"
        ).fetchall()
        assert [item["key"] for item in audits] == [
            "platform_styles.xhs.hashtags",
            "platform_styles.xhs.title_style",
        ]
    finally:
        conn.close()


def test_update_platform_style_rejects_unknown_platform():
    client, conn = _client()
    try:
        response = client.patch("/api/platform_styles/unknown", json={"tone": "简洁"})
        assert response.status_code == 400
    finally:
        conn.close()
