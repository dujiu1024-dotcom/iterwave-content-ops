import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.audit_queries import build_audit_query_router


def _client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ai_calls (
            id INTEGER PRIMARY KEY, step TEXT NOT NULL, called_at TEXT NOT NULL
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, round_number INTEGER NOT NULL
        );
        CREATE TABLE user_override (
            id INTEGER PRIMARY KEY, round_id INTEGER, override_field TEXT,
            original_value TEXT, override_value TEXT, reason TEXT,
            overridden_by TEXT, overridden_at TEXT NOT NULL
        );
        INSERT INTO ai_calls(step, called_at) VALUES
            ('draft', '2026-11-02T10:00:00'),
            ('topic', '2026-11-02T11:00:00'),
            ('draft', '2026-11-02T12:00:00');
        INSERT INTO spiral_rounds(id, round_number) VALUES (7, 3);
        INSERT INTO user_override(
            round_id, override_field, original_value, override_value,
            reason, overridden_by, overridden_at
        ) VALUES (7, 'title', 'old', 'new', 'review', 'operator', '2026-11-02T12:00:00');
    """)
    app = FastAPI()
    app.include_router(build_audit_query_router(lambda: {"conn": conn}))
    return TestClient(app), conn


def test_ai_calls_query_filters_by_step_and_orders_newest_first():
    client, conn = _client()
    try:
        response = client.get("/api/ai_calls?step=draft&limit=1")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["step"] == "draft"
        assert response.json()[0]["called_at"] == "2026-11-02T12:00:00"
    finally:
        conn.close()


def test_user_override_query_includes_business_round_number():
    client, conn = _client()
    try:
        response = client.get("/api/user_override")
        assert response.status_code == 200
        assert response.json() == [{
            "id": 1,
            "round_id": 7,
            "override_field": "title",
            "original_value": "old",
            "override_value": "new",
            "reason": "review",
            "overridden_by": "operator",
            "overridden_at": "2026-11-02T12:00:00",
            "round_number": 3,
        }]
    finally:
        conn.close()
