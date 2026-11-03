from datetime import datetime

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.adapters.base import Platform
from src.api.action_items import build_action_items_router


def _client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY, goal_text TEXT, target_platform TEXT,
            target_metric TEXT, target_value REAL, metric_scope TEXT,
            baseline_value REAL, baseline_at TEXT, current_value REAL,
            metric_period_end TEXT, status TEXT
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, round_number INTEGER, status TEXT,
            awaiting_publish_ended_at TEXT, created_at TEXT
        );
        CREATE TABLE circuit_breaker_events (
            id INTEGER PRIMARY KEY, platform TEXT, reason TEXT, detected_at TEXT,
            cooldown_until TEXT, resolved_at TEXT
        );
        INSERT INTO goals VALUES
            (1, '提升互动率', 'xhs', 'interaction_rate', 10, 'platform',
             2, '2026-10-01', 5, '2026-11-02', 'active'),
            (2, '已完成目标', 'douyin', 'followers', 100, 'platform',
             50, '2026-10-01', 100, '2026-11-02', 'active');
        INSERT INTO spiral_rounds VALUES
            (7, 3, 'awaiting_publish', '2026-11-02T12:00:00', '2026-11-01T12:00:00');
        INSERT INTO circuit_breaker_events VALUES
            (1, 'xhs', 'rate_limit', '2026-11-02T10:00:00', '2026-11-03T11:00:00', NULL),
            (2, 'douyin', 'challenge', '2026-11-02T10:00:00', '2026-11-03T13:00:00', NULL),
            (3, 'kuaishou', 'manual', '2026-11-02T10:00:00', NULL, NULL),
            (4, 'wechat_channels', 'resolved', '2026-11-02T10:00:00', '2026-11-03T10:00:00', '2026-11-03T10:30:00');
    """)
    now = datetime.fromisoformat("2026-11-03T12:00:00")
    app = FastAPI()
    app.include_router(build_action_items_router(
        lambda: {"conn": conn},
        lambda: now,
        Platform,
        lambda row: 50 if row["id"] == 1 else 100,
    ))
    return TestClient(app), conn


def test_action_items_aggregates_only_actionable_goal_round_and_breaker_rows():
    client, conn = _client()
    try:
        response = client.get("/api/action_items")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert [item["id"] for item in body["items"]] == [
            "goal:1", "round:7", "cb:1", "cb2:2", "cb2:3",
        ]
        assert body["items"][0]["type"] == "goal_strategy"
        assert "50%" in body["items"][0]["title"]
        assert body["items"][1]["type"] == "publish_decision"
        assert body["items"][1]["detail"] == "超时 1 天"
        assert body["items"][2]["type"] == "circuit_recover"
        assert body["items"][3]["type"] == "circuit_active"
        assert body["items"][4]["type"] == "circuit_active"
    finally:
        conn.close()


def test_action_items_returns_empty_list_when_no_actions_exist():
    client, conn = _client()
    try:
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM spiral_rounds")
        conn.execute("DELETE FROM circuit_breaker_events")
        response = client.get("/api/action_items")
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}
    finally:
        conn.close()
