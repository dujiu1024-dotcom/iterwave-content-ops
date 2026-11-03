import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.adapters.base import Platform
from src.api.platform_progress import build_platform_progress_router


def _client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE drafts (id INTEGER PRIMARY KEY, platform TEXT NOT NULL, status TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO drafts(platform, status) VALUES (?, ?)",
        [("xhs", "draft_pending"), ("xhs", "published"), ("douyin", "draft_pending")],
    )
    context = {"conn": conn, "cfg": object()}
    snapshots = {
        platform.value: {"enabled": platform.value == "xhs", "circuit_breaker": {"open": False}}
        for platform in Platform
    }
    app = FastAPI()
    app.include_router(build_platform_progress_router(
        lambda: context, Platform, lambda _cfg, _conn: snapshots,
    ))
    return TestClient(app), conn


def test_platform_progress_returns_all_platforms_and_status_breakdown():
    client, conn = _client()
    try:
        response = client.get("/api/platform_progress")
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {platform.value for platform in Platform}
        assert data["xhs"]["enabled"] is True
        assert data["xhs"]["total_drafts"] == 2
        assert data["xhs"]["status_breakdown"] == {
            "draft_pending": 1,
            "published": 1,
        }
        assert data["douyin"]["enabled"] is False
        assert data["douyin"]["total_drafts"] == 1
        assert data["kuaishou"]["total_drafts"] == 0
        assert data["wechat_channels"]["status_breakdown"] == {}
    finally:
        conn.close()
