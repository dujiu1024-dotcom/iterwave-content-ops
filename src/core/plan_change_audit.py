"""Append-only audit records and immutable task-impact snapshots for plan changes."""
from __future__ import annotations

import json
from typing import Any

from .task_context import current_task_identity


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def load_version_snapshot(conn, entity_type: str, version_id: int) -> dict | None:
    if entity_type == "master_plan":
        row = conn.execute(
            "SELECT plan_snapshot FROM master_plan_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["plan_snapshot"])
    if entity_type == "sub_plan":
        row = conn.execute(
            "SELECT sub_plan_id,snapshot_json FROM sub_plan_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        snapshot = json.loads(row["snapshot_json"])
        snapshot["stages"] = [
            dict(item) for item in conn.execute(
                """SELECT stage_key,stage_order,title,direction,target_count,start_date,end_date,
                          completion_condition,status,notes,strategy_goal,current_problem,
                          execution_strategy,prerequisites,input_info,expected_result,
                          completion_criteria,effect_evaluation,entry_condition,
                          entry_condition_met,dependencies_json,business_completion_required,
                          effect_required
                   FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order,id""",
                (version_id,),
            ).fetchall()
        ]
        return snapshot
    if entity_type == "production_line":
        row = conn.execute(
            "SELECT definition_json FROM production_line_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["definition_json"])
    raise ValueError("不支持的计划类型")


def analyze_task_impact(conn, entity_type: str, version_id: int | None) -> dict:
    if version_id is None:
        return {
            "tasks": [], "counts": {}, "round_ids": [],
            "reassessment": {
                "completed": [], "in_progress": [], "not_started": [],
                "needs_review": [], "cancelled": [],
            },
        }
    if entity_type == "master_plan":
        task_sql = "SELECT id,round_id,status,attempt_count,content_id FROM production_tasks WHERE master_plan_version_id=? ORDER BY created_at,id"
        round_sql = "SELECT id FROM spiral_rounds WHERE master_plan_version_id=? ORDER BY id"
    elif entity_type == "sub_plan":
        task_sql = "SELECT id,round_id,status,attempt_count,content_id FROM production_tasks WHERE sub_plan_version_id=? ORDER BY created_at,id"
        round_sql = "SELECT id FROM spiral_rounds WHERE sub_plan_version_id=? ORDER BY id"
    elif entity_type == "production_line":
        task_sql = "SELECT id,round_id,status,attempt_count,content_id FROM production_tasks WHERE production_line_version_id=? ORDER BY created_at,id"
        round_sql = "SELECT id FROM spiral_rounds WHERE production_line_version_id=? ORDER BY id"
    else:
        raise ValueError("不支持的计划类型")
    def table_columns(table: str) -> set[str]:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            return set()
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}

    task_columns = table_columns("production_tasks")
    required_task_columns = {"id", "round_id", "status", "attempt_count", "content_id",
                             "created_at", f"{entity_type}_version_id"}
    # Older schema generations may have the task table but not yet the version
    # provenance columns. Treat that as unavailable provenance, never as a
    # partially valid query that breaks version creation or approval.
    tasks = (
        [dict(row) for row in conn.execute(task_sql, (version_id,)).fetchall()]
        if required_task_columns.issubset(task_columns) else []
    )
    counts: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    round_columns = table_columns("spiral_rounds")
    required_round_columns = {"id", f"{entity_type}_version_id"}
    rounds = (
        [int(row["id"]) for row in conn.execute(round_sql, (version_id,)).fetchall()]
        if required_round_columns.issubset(round_columns) else []
    )
    reassessment = {
        "completed": [task["id"] for task in tasks if task["status"] == "succeeded"],
        "in_progress": [task["id"] for task in tasks if task["status"] == "running"],
        "not_started": [task["id"] for task in tasks if task["status"] == "pending"],
        "needs_review": [task["id"] for task in tasks if task["status"] == "failed"],
        "cancelled": [task["id"] for task in tasks if task["status"] == "cancelled"],
    }
    return {"tasks": tasks, "counts": counts, "round_ids": rounds, "reassessment": reassessment}


def record_plan_change(
    conn,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    from_version_id: int | None,
    to_version_id: int,
    change_reason: str,
    actor: str,
    before_snapshot: dict | None = None,
    after_snapshot: dict | None = None,
) -> dict:
    """Record a version transition without mutating either version or its tasks."""
    if after_snapshot is None:
        after_snapshot = load_version_snapshot(conn, entity_type, to_version_id)
    if before_snapshot is None and from_version_id is not None:
        before_snapshot = load_version_snapshot(conn, entity_type, from_version_id)
    impact = analyze_task_impact(conn, entity_type, from_version_id)
    cursor = conn.execute(
        """INSERT INTO plan_change_audit(
               entity_type,entity_id,event_type,from_version_id,to_version_id,
               change_reason,before_snapshot_json,after_snapshot_json,
               affected_tasks_json,actor,initiating_actor,executing_actor,tenant_id,queue_task_id,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))""",
        (entity_type, str(entity_id), event_type, from_version_id, to_version_id,
         str(change_reason or "")[:2000],
         _json(before_snapshot) if before_snapshot is not None else None,
         _json(after_snapshot or {}), _json(impact), str(actor or "system")[:120],
         (identity := current_task_identity()).get("initiating_actor"),
         identity.get("executing_actor"), identity.get("tenant_id"), identity.get("task_id")),
    )
    return {"audit_id": int(cursor.lastrowid), "impact": impact}
