"""Dashboard aggregation for per-platform draft progress."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter


def build_platform_progress_router(
    get_context: Callable[[], dict],
    platform_enum,
    platform_status_snapshot: Callable,
) -> APIRouter:
    """Build the read-only four-platform progress endpoint."""
    router = APIRouter()

    @router.get("/api/platform_progress")
    def platform_progress():
        """Return enabled state, total drafts, status counts and breaker state per platform."""
        context = get_context()
        conn = context["conn"]
        cfg = context["cfg"]
        platform_status = platform_status_snapshot(cfg, conn)
        result = {}
        for platform in (item.value for item in platform_enum):
            status_rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM drafts "
                "WHERE platform=? GROUP BY status",
                (platform,),
            ).fetchall()
            status_map = {row[0]: row[1] for row in status_rows}
            result[platform] = {
                **platform_status[platform],
                "total_drafts": sum(status_map.values()),
                "status_breakdown": status_map,
            }
        return result

    return router
