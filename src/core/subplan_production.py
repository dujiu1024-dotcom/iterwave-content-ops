"""Stage-driven production eligibility for approved platform subplans."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from .plan_version_store import PlanVersionStore


def production_paused(conn) -> bool:
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key='production_paused'").fetchone()
    except Exception as exc:
        # 无法确认暂停开关状态时必须 fail closed，不能把数据库故障解释为允许生产。
        raise RuntimeError("无法读取内容生产暂停状态，已按安全策略阻止生产") from exc
    return bool(row) and str(row[0]).strip().lower() in {"1", "true", "yes", "on"}


def active_subplan_stage(conn, *, today: str | None = None, advance: bool = False) -> dict[str, Any] | None:
    """Return the first executable stage across active platform subplans."""
    today = today or date.today().isoformat()
    identities = conn.execute(
        """SELECT p.id AS sub_plan_id,p.platform,p.title AS subplan_title,
                  p.master_plan_version_id,p.current_version_id,
                  v.version_no,v.snapshot_json,m.plan_signature,
                  pl.status AS production_line_status,
                  pl.current_version_id AS production_line_version_id,
                  plv.status AS production_line_version_status,
                  plv.sub_plan_version_id AS production_line_subplan_version_id,
                  plv.definition_json AS production_line_definition_json
           FROM sub_plans p
           JOIN sub_plan_versions v ON v.id=p.current_version_id AND v.status='effective'
           JOIN master_plan_versions m ON m.id=p.master_plan_version_id AND m.status='effective'
           LEFT JOIN production_lines pl ON pl.sub_plan_id=p.id
           LEFT JOIN production_line_versions plv ON plv.id=pl.current_version_id
           WHERE p.status='active'
           ORDER BY p.updated_at,p.id"""
    ).fetchall()
    first_blocker = None
    if not identities:
        superseded = conn.execute(
            """SELECT p.id,p.master_plan_version_id FROM sub_plans p
               JOIN master_plan_versions m ON m.id=p.master_plan_version_id
               WHERE p.status IN ('active','paused') AND m.status!='effective'
               ORDER BY p.updated_at DESC LIMIT 1"""
        ).fetchone()
        if superseded:
            return {"blocker": "master_version_superseded",
                    "sub_plan_id": superseded["id"],
                    "master_plan_version_id": superseded["master_plan_version_id"]}
        paused = conn.execute(
            "SELECT id FROM sub_plans WHERE status='paused' AND current_version_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if paused:
            return {"blocker": "subplan_paused", "sub_plan_id": paused["id"]}
    for identity in identities:
        if identity["production_line_status"] is None:
            return {"blocker": "production_line_missing", "sub_plan_id": identity["sub_plan_id"]}
        if identity["production_line_status"] == "paused":
            return {"blocker": "production_line_paused", "sub_plan_id": identity["sub_plan_id"]}
        if (identity["production_line_status"] != "active"
                or identity["production_line_version_id"] is None
                or identity["production_line_version_status"] != "effective"
                or identity["production_line_subplan_version_id"] != identity["current_version_id"]):
            return {"blocker": "production_line_not_effective", "sub_plan_id": identity["sub_plan_id"]}
        stages = conn.execute(
            """SELECT s.*,COALESCE(stats.confirmed_published_count,0) AS confirmed_published_count
               FROM sub_plan_stages s
               LEFT JOIN sub_plan_stage_publish_stats stats ON stats.stage_id=s.id
               WHERE s.sub_plan_version_id=? ORDER BY s.stage_order,s.id""",
            (identity["current_version_id"],),
        ).fetchall()
        for row in stages:
            stage = dict(row)
            if stage["status"] == "cancelled":
                continue
            count = int(stage["confirmed_published_count"] or 0)
            from .strategy_step_completion import completion_gate_state
            gates = completion_gate_state(stage)
            if gates["production_blocked"]:
                stage["blocker"] = "strategy_condition_requires_adjustment"
                stage["completion_gates"] = gates
                stage.update({key: identity[key] for key in identity.keys() if key != "id"})
                first_blocker = first_blocker or stage
                break
            if stage["status"] == "completed":
                continue
            if count >= int(stage["target_count"]):
                from .strategy_step_completion import completion_gate_state
                gates = completion_gate_state(stage)
                stage["completion_gates"] = gates
                stage["remaining_count"] = 0
                if not advance or not gates["can_complete"]:
                    stage["blocker"] = (
                        "stage_target_reached" if not advance
                        else "stage_completion_conditions_not_met"
                    )
                    stage.update({key: identity[key] for key in identity.keys() if key != "id"})
                    first_blocker = first_blocker or stage
                    break
                lifecycle = PlanVersionStore(conn)
                if stage["status"] == "pending":
                    lifecycle.update_stage_status(int(stage["id"]), "in_progress")
                lifecycle.update_stage_status(int(stage["id"]), "completed")
                continue
            if stage["status"] == "paused":
                stage["blocker"] = "stage_paused"
                stage.update({key: identity[key] for key in identity.keys() if key != "id"})
                first_blocker = first_blocker or stage
                break
            start_date, end_date = stage.get("start_date"), stage.get("end_date")
            if start_date and today < start_date:
                stage["blocker"] = "stage_not_started"
                stage["next_eligible_at"] = start_date
                first_blocker = first_blocker or stage
                break
            if end_date and today > end_date:
                stage["blocker"] = "stage_window_expired"
                first_blocker = first_blocker or stage
                break
            stage.update({key: identity[key] for key in identity.keys() if key != "id"})
            stage["remaining_count"] = max(0, int(stage["target_count"]) - count)
            try:
                line_definition = json.loads(identity["production_line_definition_json"] or "{}")
                line_stage = next(
                    item for item in line_definition.get("stages", [])
                    if item.get("stage_key") == stage["stage_key"]
                )
                stage["production_line_batch_size"] = int(line_stage["batch_size"])
                stage["production_line_stage"] = line_stage
            except (TypeError, ValueError, KeyError, StopIteration):
                return {"blocker": "production_line_stage_invalid",
                        "sub_plan_id": identity["sub_plan_id"]}
            if stage["production_line_batch_size"] < 1:
                return {"blocker": "production_line_stage_invalid",
                        "sub_plan_id": identity["sub_plan_id"]}
            return stage
        # A platform subplan becomes completed only when every stage reached its
        # confirmed-publish target; cancelled/paused stages are not treated as success.
        status_counts = conn.execute(
            "SELECT COUNT(*),SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) "
            "FROM sub_plan_stages WHERE sub_plan_version_id=?",
            (identity["current_version_id"],),
        ).fetchone()
        total = int(status_counts[0] or 0) if status_counts else 0
        completed = int(status_counts[1] or 0) if status_counts else 0
        cancelled = int(status_counts[2] or 0) if status_counts else 0
        if advance and total > 0 and completed + cancelled == total:
            terminal_status = "completed" if completed == total else "cancelled"
            conn.execute(
                "UPDATE sub_plans SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
                (terminal_status, identity["sub_plan_id"]),
            )
            conn.execute(
                """UPDATE production_lines SET status=?,updated_at=CURRENT_TIMESTAMP
                   WHERE sub_plan_id=? AND status IN ('active','paused')""",
                (terminal_status, identity["sub_plan_id"]),
            )
            conn.commit()
        # Continue to the next platform subplan if this one has no executable stage.
    return first_blocker


def stage_open_blocker(conn, stage: dict[str, Any] | None) -> dict[str, Any] | None:
    if production_paused(conn):
        return {"code": "production_paused", "reason": "内容生产已暂停"}
    if stage is None:
        return {"code": "no_active_subplan_stage", "reason": "没有可执行的已审核子计划阶段"}
    if stage.get("blocker") == "stage_not_started":
        return {"code": "stage_not_started", "reason": "当前阶段尚未到执行日期",
                "next_eligible_at": stage.get("next_eligible_at")}
    if stage.get("blocker") == "stage_window_expired":
        return {"code": "stage_window_expired", "reason": "当前阶段执行窗口已结束"}
    if stage.get("blocker") == "stage_paused":
        return {"code": "stage_paused", "reason": "当前阶段已暂停"}
    if stage.get("blocker") == "stage_completion_conditions_not_met":
        return {"code": "stage_completion_conditions_not_met", "reason": "发布数量已达标，但业务完成或效果条件尚未通过",
                "completion_gates": stage.get("completion_gates")}
    if stage.get("blocker") == "stage_completion_conditions_pending":
        return {"code": "stage_completion_conditions_pending", "reason": "策略步骤完成条件待确认",
                "completion_gates": stage.get("completion_gates")}
    if stage.get("blocker") == "strategy_condition_requires_adjustment":
        return {"code": "strategy_condition_requires_adjustment", "reason": "策略条件需要调整"}
    if stage.get("blocker") == "production_line_missing":
        return {"code": "production_line_missing", "reason": "当前子计划尚未建立并确认生产线"}
    if stage.get("blocker") == "production_line_paused":
        return {"code": "production_line_paused", "reason": "当前生产线已暂停"}
    if stage.get("blocker") == "production_line_not_effective":
        return {"code": "production_line_not_effective", "reason": "当前生产线未生效或绑定版本已过期"}
    if stage.get("blocker") == "production_line_stage_invalid":
        return {"code": "production_line_stage_invalid", "reason": "生产线未正确配置当前策略步骤"}
    if stage.get("blocker") == "subplan_paused":
        return {"code": "subplan_paused", "reason": "子计划已暂停"}
    if int(stage.get("remaining_count") or 0) <= 0:
        return {"code": "stage_target_reached", "reason": "当前阶段确认发布目标已达成"}
    return None


def list_production_line(conn, *, sub_plan_id=None, platform=None, stage_id=None,
                         version_id=None, round_status=None, limit=50, offset=0):
    clauses, args = [], []
    if sub_plan_id:
        clauses.append("r.sub_plan_id=?"); args.append(sub_plan_id)
    if platform:
        clauses.append("p.platform=?"); args.append(platform)
    if stage_id is not None:
        clauses.append("r.sub_plan_stage_id=?"); args.append(stage_id)
    if version_id is not None:
        clauses.append("r.sub_plan_version_id=?"); args.append(version_id)
    if round_status:
        clauses.append("r.status=?"); args.append(round_status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        """SELECT r.id AS round_id,r.round_number,r.status,r.created_at,
                  r.master_plan_version_id,r.sub_plan_id,r.sub_plan_version_id,
                  r.sub_plan_stage_id,r.sub_plan_snapshot,r.sub_plan_stage_snapshot,
                  p.platform,s.stage_order,s.title AS stage_title,
                  COALESCE(stats.confirmed_published_count,0) AS confirmed_published_count
           FROM spiral_rounds r
           LEFT JOIN sub_plans p ON p.id=r.sub_plan_id
           LEFT JOIN sub_plan_stages s ON s.id=r.sub_plan_stage_id
           LEFT JOIN sub_plan_stage_publish_stats stats ON stats.stage_id=s.id"""
        + where + " ORDER BY r.id DESC LIMIT ? OFFSET ?",
        [*args, max(1, min(int(limit), 100)), max(0, int(offset))],
    ).fetchall()
    return [dict(row) for row in rows]
