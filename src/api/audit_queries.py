"""Read-only audit query routes."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Query


def build_audit_query_router(get_context: Callable[[], dict]) -> APIRouter:
    """Build audit read APIs using the active tenant database."""
    router = APIRouter()

    @router.get("/api/ai_calls")
    def list_ai_calls(limit: int = Query(200, ge=1, le=2000), step: str | None = None):
        conn = get_context()["conn"]
        sql = "SELECT * FROM ai_calls"
        args: list = []
        if step:
            sql += " WHERE step = ?"
            args.append(step)
        sql += " ORDER BY called_at DESC LIMIT ?"
        args.append(limit)
        return [dict(row) for row in conn.execute(sql, args).fetchall()]

    @router.get("/api/user_override")
    def list_user_override(limit: int = Query(200, ge=1, le=2000)):
        conn = get_context()["conn"]
        rows = conn.execute(
            "SELECT u.*, r.round_number FROM user_override u "
            "LEFT JOIN spiral_rounds r ON r.id = u.round_id "
            "ORDER BY u.overridden_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    return router
