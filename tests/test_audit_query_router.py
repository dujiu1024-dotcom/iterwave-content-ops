import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.audit_queries import build_audit_query_router


def _client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ai_calls (
            id INTEGER PRIMARY KEY, step TEXT NOT NULL, module TEXT, model TEXT,
            prompt_version TEXT, algo_version TEXT, input_digest TEXT,
            tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
            duration_ms INTEGER, output_valid INTEGER, success INTEGER,
            attempt INTEGER, called_at TEXT NOT NULL, internal_worker_trace TEXT
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, round_number INTEGER NOT NULL
        );
        CREATE TABLE user_override (
            id INTEGER PRIMARY KEY, round_id INTEGER, override_field TEXT,
            original_value TEXT, override_value TEXT, reason TEXT,
            overridden_by TEXT, overridden_at TEXT NOT NULL, internal_audit_blob TEXT
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


def test_audit_query_apis_do_not_leak_future_internal_columns():
    client, conn = _client()
    try:
        conn.execute("ALTER TABLE ai_calls ADD COLUMN internal_worker_trace TEXT")
        conn.execute("UPDATE ai_calls SET internal_worker_trace='private worker diagnostics'")
        conn.execute("ALTER TABLE user_override ADD COLUMN internal_audit_blob TEXT")
        conn.execute("UPDATE user_override SET internal_audit_blob='private override metadata'")
        conn.commit()

        ai = client.get("/api/ai_calls?limit=1")
        assert ai.status_code == 200
        assert "internal_worker_trace" not in ai.text
        assert "private worker diagnostics" not in ai.text
        assert set(ai.json()[0]) == {
            "id", "step", "module", "model", "prompt_version", "algo_version",
            "input_digest", "tokens_in", "tokens_out", "latency_ms",
            "duration_ms", "output_valid", "success", "attempt", "called_at",
        }

        overrides = client.get("/api/user_override")
        assert overrides.status_code == 200
        assert "internal_audit_blob" not in overrides.text
        assert "private override metadata" not in overrides.text
        assert "round_number" in overrides.json()[0]
        assert "round_id" not in overrides.json()[0]
        assert "ai_call_id" not in overrides.json()[0]
    finally:
        conn.close()
