import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.hot_sources import build_hot_sources_router
from src.core import kb_sources
from src.core.redact import redact
from src.core.config_store import ConfigNotAllowedError, ConfigPatchError


def _client(source_type="rss", fetch_error=None):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE hot_source_test_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            source_label TEXT NOT NULL,
            source_type TEXT NOT NULL,
            status TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            sample_titles_json TEXT NOT NULL,
            message TEXT NOT NULL,
            tested_at TEXT NOT NULL
        )
    """)
    source = {
        "name": "行业 RSS",
        "type": source_type,
        "platform": "xhs",
        "url": "https://example.com/feed?token=secret-value",
        "platforms": ["xhs"],
    }
    cfg = SimpleNamespace(hot_topics=SimpleNamespace(sources=[source]))
    context = {"conn": conn, "cfg": cfg}

    class Collector:
        def __init__(self, _cfg, _conn):
            pass

        def _fetch_custom(self, _source, _label):
            if fetch_error:
                raise RuntimeError(fetch_error)
            return [{"title": "安全标题一"}, {"title": "安全标题二"}]

    def patch(_cfg, _conn, updates):
        _cfg.hot_topics.sources = updates["hot_topics.sources"]

    app = FastAPI()
    app.include_router(build_hot_sources_router(
        lambda: context, patch, ConfigNotAllowedError, ConfigPatchError,
        Collector, redact, kb_sources._redact_remote_url,
        lambda: datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
    ))
    return TestClient(app), conn, cfg, source


def test_hot_source_list_redacts_url_and_includes_latest_test():
    client, conn, _cfg, source = _client()
    try:
        tested = client.post("/api/hot_topics/sources/0/test")
        assert tested.status_code == 200
        assert tested.json()["ok"] is True

        listed = client.get("/api/hot_topics/sources")
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        assert "secret-value" not in item["url"]
        assert item["last_test"]["status"] == "success"
        assert item["last_test"]["item_count"] == 2

        history = client.get("/api/hot_topics/sources/history")
        assert history.status_code == 200
        assert history.json()["total"] == 1
        assert "secret-value" not in str(history.json())
        stored = conn.execute(
            "SELECT source_key, sample_titles_json FROM hot_source_test_history"
        ).fetchone()
        assert stored["source_key"] != source["url"]
        assert "安全标题一" in stored["sample_titles_json"]
    finally:
        conn.close()


def test_hot_source_update_restores_existing_masked_url():
    client, conn, cfg, source = _client()
    try:
        public = client.get("/api/hot_topics/sources").json()["items"][0]
        response = client.put("/api/hot_topics/sources", json={
            "items": [{
                "name": public["name"],
                "type": public["type"],
                "platform": public["platform"],
                "platforms": public["platforms"],
                "url": public["url"],
            }],
        })
        assert response.status_code == 200
        assert cfg.hot_topics.sources[0]["url"] == source["url"]
        assert "secret-value" not in response.text
    finally:
        conn.close()


def test_hot_source_test_rejects_invalid_index_and_aggregate_source():
    client, conn, _cfg, _source = _client()
    try:
        assert client.post("/api/hot_topics/sources/4/test").status_code == 404
        response = client.put("/api/hot_topics/sources", json={"items": "not-an-array"})
        assert response.status_code == 422
    finally:
        conn.close()

    client, conn, _cfg, _source = _client(source_type="aggregate")
    try:
        assert client.post("/api/hot_topics/sources/0/test").status_code == 400
    finally:
        conn.close()


def test_hot_source_fetch_error_is_sanitized_and_persisted():
    client, conn, _cfg, _source = _client(
        fetch_error="Authorization: Bearer abcdefghijklmnop",
    )
    try:
        response = client.post("/api/hot_topics/sources/0/test")
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert "abcdefghijklmnop" not in response.text
        stored = conn.execute(
            "SELECT message FROM hot_source_test_history"
        ).fetchone()
        assert stored is not None
        assert "abcdefghijklmnop" not in stored["message"]
    finally:
        conn.close()


def test_hot_source_update_restores_url_for_matching_name_when_masked_urls_collide():
    client, conn, cfg, first_source = _client()
    second_source = {
        **first_source,
        "name": "第二来源",
        "url": "https://example.com/feed?token=other-secret",
    }
    cfg.hot_topics.sources = [first_source, second_source]
    try:
        public = client.get("/api/hot_topics/sources").json()["items"]
        response = client.put("/api/hot_topics/sources", json={"items": [public[1]]})
        assert response.status_code == 200
        assert cfg.hot_topics.sources[0]["url"] == second_source["url"]
        assert "secret-value" not in response.text
        assert "other-secret" not in response.text
    finally:
        conn.close()
