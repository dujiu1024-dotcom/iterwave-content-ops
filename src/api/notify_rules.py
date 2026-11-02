"""Notification-rule API router and request schema."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable

from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel, field_validator


NOTIFY_RULES_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario   TEXT NOT NULL,
    channel    TEXT NOT NULL CHECK (channel IN ('macos', 'wecom', 'feishu', 'console')),
    level      TEXT NOT NULL DEFAULT 'warning'
               CHECK (level IN ('info', 'warning', 'error', 'critical')),
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    condition  TEXT,
    notes      TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notify_rules_scenario_channel
    ON notify_rules (scenario, channel);
"""


class NotifyRuleRequest(BaseModel):
    scenario: str
    channel: str
    level: str
    enabled: bool = True
    condition: str | None = None
    notes: str | None = None
    platforms: list[str] | None = None

    @field_validator("scenario")
    @classmethod
    def _scenario_ok(cls, value: str) -> str:
        text = value.strip()
        if not text or len(text) > 64:
            raise ValueError("scenario 必须是 1-64 字的场景标识")
        return text

    @field_validator("channel")
    @classmethod
    def _channel_enum(cls, value: str) -> str:
        if value not in ("macos", "wecom", "feishu", "console"):
            raise ValueError("channel 只能是 macos / wecom / feishu / console")
        return value

    @field_validator("level")
    @classmethod
    def _level_enum(cls, value: str) -> str:
        if value not in ("info", "warning", "error", "critical"):
            raise ValueError("level 只能是 info / warning / error / critical")
        return value


def build_notify_rules_router(
    get_context: Callable[[], dict],
    now_provider: Callable[[], datetime],
    *,
    route_class: type[APIRoute] = APIRoute,
) -> APIRouter:
    """Build notification CRUD routes with explicit runtime dependencies."""
    router = APIRouter(route_class=route_class)

    @router.get("/api/notify_rules")
    def list_notify_rules():
        conn = get_context()["conn"]
        try:
            rows = conn.execute("SELECT * FROM notify_rules ORDER BY id ASC").fetchall()
        except sqlite3.OperationalError:
            conn.executescript(NOTIFY_RULES_SCHEMA)
            conn.commit()
            rows = []
        return [dict(row) for row in rows]

    @router.post("/api/notify_rules")
    def create_notify_rule(req: NotifyRuleRequest):
        conn = get_context()["conn"]
        try:
            conn.execute("SELECT 1 FROM notify_rules LIMIT 1")
        except sqlite3.OperationalError:
            conn.executescript(NOTIFY_RULES_SCHEMA)
            conn.commit()
        now = now_provider().isoformat(timespec="seconds")
        existing = conn.execute(
            "SELECT id FROM notify_rules WHERE scenario=? AND channel=?",
            (req.scenario, req.channel),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE notify_rules SET level=?, enabled=?, condition=?, notes=?,
                          updated_at=? WHERE id=?""",
                (req.level, 1 if req.enabled else 0, req.condition, req.notes,
                 now, existing[0]),
            )
            conn.commit()
            return {"id": existing[0], "message": "同场景同通道规则已存在，已更新"}
        cur = conn.execute(
            """INSERT INTO notify_rules (scenario, channel, level, enabled, condition, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (req.scenario, req.channel, req.level, 1 if req.enabled else 0,
             req.condition, req.notes, now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "message": "创建成功"}

    @router.patch("/api/notify_rules/{rule_id}")
    def update_notify_rule(rule_id: int, req: NotifyRuleRequest):
        conn = get_context()["conn"]
        conn.execute(
            """UPDATE notify_rules SET scenario=?, channel=?, level=?, enabled=?,
                      condition=?, notes=?, updated_at=?
               WHERE id=?""",
            (req.scenario, req.channel, req.level, 1 if req.enabled else 0,
             req.condition, req.notes, now_provider().isoformat(timespec="seconds"), rule_id),
        )
        conn.commit()
        return {"id": rule_id, "message": "更新成功"}

    @router.delete("/api/notify_rules/{rule_id}")
    def delete_notify_rule(rule_id: int):
        conn = get_context()["conn"]
        conn.execute("DELETE FROM notify_rules WHERE id=?", (rule_id,))
        conn.commit()
        return {"message": "删除成功"}

    return router
