"""Shared strategy-step completion gates.

Publishing quantity is necessary but never sufficient when a step declares
business completion or effect-review requirements.
"""
from __future__ import annotations

from typing import Any


def completion_gate_state(stage: dict[str, Any]) -> dict[str, Any]:
    count = int(stage.get("confirmed_published_count") or 0)
    target = int(stage.get("target_count") or 0)
    quantity_passed = count >= target
    business_required = bool(stage.get("business_completion_required"))
    effect_required = bool(stage.get("effect_required"))
    business_status = stage.get("business_completion_status") or ("pending" if business_required else "not_required")
    effect_status = stage.get("effect_status") or ("pending" if effect_required else "not_required")
    blockers = []
    if not quantity_passed:
        blockers.append("quantity_target_pending")
    if business_required and business_status != "passed":
        blockers.append("business_completion_" + str(business_status))
    if effect_required and effect_status != "passed":
        blockers.append("effect_" + str(effect_status))
    return {
        "quantity_status": "passed" if quantity_passed else "pending",
        "quantity_passed": quantity_passed,
        "business_status": business_status,
        "business_required": business_required,
        "effect_status": effect_status,
        "effect_required": effect_required,
        "can_complete": not blockers,
        "production_blocked": (
            (business_required and business_status in {"failed", "needs_review"})
            or (effect_required and effect_status in {"failed", "needs_review"})
        ),
        "blockers": blockers,
    }


def assert_stage_completion_ready(conn, stage_id: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT s.*, COALESCE(stats.confirmed_published_count,0) AS confirmed_published_count
           FROM sub_plan_stages s
           LEFT JOIN sub_plan_stage_publish_stats stats ON stats.stage_id=s.id
           WHERE s.id=?""", (stage_id,)
    ).fetchone()
    if row is None:
        raise ValueError("策略步骤不存在")
    state = completion_gate_state(dict(row))
    if not state["can_complete"]:
        labels = {
            "quantity_target_pending": "发布数量目标尚未达标",
            "business_completion_pending": "业务完成条件尚未确认",
            "business_completion_needs_review": "业务完成条件等待人工审核",
            "business_completion_failed": "业务完成条件未通过，需要调整策略",
            "effect_pending": "效果评估尚未完成",
            "effect_needs_review": "效果评估等待人工审核",
            "effect_failed": "效果未达标，需要调整策略",
        }
        reasons = [labels.get(item, item) for item in state["blockers"]]
        raise ValueError("策略步骤不能完成：" + "；".join(reasons))
    return dict(row) | {"completion_gates": state}
