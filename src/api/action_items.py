"""Dashboard action items aggregation."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import APIRouter

from src.adapters.base import circuit_reason_guidance, circuit_reason_label


def build_action_items_router(
    get_context: Callable[[], dict],
    now_provider: Callable,
    platform_enum,
    goal_progress_rate_for: Callable,
) -> APIRouter:
    """Build the dashboard's human-decision queue from tenant-scoped data."""
    router = APIRouter()

    @router.get("/api/action_items")
    def action_items():
        """Aggregate overdue goals, publish decisions and circuit-breaker actions."""
        conn = get_context()["conn"]
        now = now_provider()
        items: list[dict] = []

        def platform_name(platform: str | None) -> str:
            if not platform:
                return "全平台"
            try:
                return platform_enum(platform).display_name
            except ValueError:
                return platform

        goal_rows = conn.execute(
            """SELECT id, goal_text, target_platform, target_metric, target_value,
                      metric_scope, baseline_value, baseline_at, current_value, metric_period_end
               FROM goals
               WHERE status='active'
                 AND metric_period_end IS NOT NULL
                 AND substr(metric_period_end, 1, 10) < ?
            """,
            (now.date().isoformat(),),
        ).fetchall()
        for row in goal_rows:
            rate = goal_progress_rate_for(row)
            if rate is not None and rate >= 100:
                continue
            percentage = f"{rate:.0f}%" if rate is not None else "数据待采集"
            current_value = row["current_value"]
            items.append({
                "id": f"goal:{row['id']}",
                "type": "goal_strategy",
                "title": (
                    f"目标「{row['goal_text']}」已过截止日期 "
                    f"{row['metric_period_end'][:10]} 但未达成（{percentage}）"
                ),
                "detail": (
                    f"{platform_name(row['target_platform'])} · {row['target_metric']} "
                    f"目标 {row['target_value']} · 当前 "
                    f"{current_value if current_value is not None else '待采集'}"
                ),
                "priority": "high",
                "source": "goals",
                "platform": row["target_platform"],
                "created_at": row["metric_period_end"],
                "actions": ["go_goal"],
            })

        rounds_rows = conn.execute(
            """SELECT id, round_number, status, awaiting_publish_ended_at, created_at
               FROM spiral_rounds
               WHERE status='awaiting_publish'
                 AND awaiting_publish_ended_at IS NOT NULL
                 AND awaiting_publish_ended_at < ?
            """,
            (now.isoformat(),),
        ).fetchall()
        for row in rounds_rows:
            items.append({
                "id": f"round:{row['id']}",
                "type": "publish_decision",
                "title": (
                    f"Round {row['round_number']} 已超过 "
                    f"{row['awaiting_publish_ended_at'][:10]} 等待发布，需确认或取消"
                ),
                "detail": (
                    f"超时 {(now - datetime.fromisoformat(row['awaiting_publish_ended_at'])).days} 天"
                ),
                "priority": "high",
                "source": "rounds",
                "created_at": row["created_at"],
                "actions": ["go_round"],
            })

        cb_rows = conn.execute(
            """SELECT id, platform, reason, detected_at, cooldown_until, resolved_at
               FROM circuit_breaker_events
               WHERE resolved_at IS NULL
                 AND cooldown_until IS NOT NULL
                 AND cooldown_until < ?
            """,
            (now.isoformat(),),
        ).fetchall()
        for row in cb_rows:
            label = circuit_reason_label(row["reason"])
            guidance = circuit_reason_guidance(row["reason"])
            items.append({
                "id": f"cb:{row['id']}",
                "type": "circuit_recover",
                "title": (
                    f"{platform_name(row['platform'])} 熔断冷却已到期"
                    f"（原因：{label}），需执行 3 步恢复验证"
                ),
                "platform": row["platform"],
                "detail": f"处理建议：{guidance}（冷却到期于 {row['cooldown_until'][:16]}）",
                "priority": "high",
                "source": "circuit_breaker",
                "created_at": row["detected_at"],
                "actions": ["run_recover"],
            })

        active_rows = conn.execute(
            """SELECT id, platform, reason, detected_at, cooldown_until
               FROM circuit_breaker_events
               WHERE resolved_at IS NULL
                 AND (cooldown_until IS NULL OR cooldown_until >= ?)
               LIMIT 5
            """,
            (now.isoformat(),),
        ).fetchall()
        for row in active_rows:
            label = circuit_reason_label(row["reason"])
            guidance = circuit_reason_guidance(row["reason"])
            items.append({
                "id": f"cb2:{row['id']}",
                "type": "circuit_active",
                "title": (
                    f"{platform_name(row['platform'])} 熔断中（原因：{label}），"
                    f"冷却至 {row['cooldown_until'][:16] if row['cooldown_until'] else '—'}"
                ),
                "platform": row["platform"],
                "detail": f"处理建议：{guidance}",
                "priority": "medium",
                "source": "circuit_breaker",
                "created_at": row["detected_at"],
                "actions": ["go_ops"],
            })

        return {"items": items, "total": len(items)}

    return router
