"""Persistent, independently recoverable content-production tasks.

A task represents one requested content item, not a strategy step or a whole
production round. Plan identifiers and the input snapshot are copied onto the
task at creation so later plan edits cannot rewrite historical provenance.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


TASK_STATUSES = {"pending", "running", "succeeded", "failed", "cancelled"}


class ProductionTaskError(ValueError):
    """Raised when a production-task transition violates its lifecycle."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _dict(row: sqlite3.Row | dict | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class ProductionTaskStore:
    """SQLite repository for durable, idempotent production work items."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_task(
        self,
        *,
        round_id: int,
        master_plan_version_id: int,
        sub_plan_id: str,
        sub_plan_version_id: int,
        strategy_step_id: str,
        platform: str,
        task_ordinal: int = 1,
        idempotency_key: str,
        input_snapshot: dict[str, Any] | None = None,
        max_attempts: int = 3,
        available_at: str | None = None,
    ) -> dict[str, Any]:
        """Create exactly one task for an idempotency key; return the existing task on replay."""
        if not idempotency_key or len(idempotency_key) > 200:
            raise ProductionTaskError("幂等键必须为 1-200 个字符")
        if not strategy_step_id or len(str(strategy_step_id)) > 128:
            raise ProductionTaskError("策略步骤必须提供稳定标识")
        if not sub_plan_id or not platform:
            raise ProductionTaskError("子计划和平台不能为空")
        if int(task_ordinal) < 1 or int(max_attempts) < 1:
            raise ProductionTaskError("任务序号和最大尝试次数必须大于零")
        if not isinstance(input_snapshot or {}, dict):
            raise ProductionTaskError("任务输入快照必须是对象")

        round_row = self.conn.execute(
            """SELECT id, master_plan_version_id, sub_plan_id,
                      sub_plan_version_id, sub_plan_stage_id,
                      sub_plan_snapshot, sub_plan_stage_snapshot
               FROM spiral_rounds WHERE id=?""",
            (round_id,),
        ).fetchone()
        if round_row is None:
            raise ProductionTaskError("生产轮次不存在")
        source = dict(round_row)
        expected = {
            "master_plan_version_id": master_plan_version_id,
            "sub_plan_id": sub_plan_id,
            "sub_plan_version_id": sub_plan_version_id,
        }
        for field, supplied in expected.items():
            actual = source.get(field)
            if actual is None or str(actual) != str(supplied):
                raise ProductionTaskError(f"任务来源与生产轮次不一致：{field}")

        # The task must point at the exact strategy step bound to this round's
        # immutable sub-plan version, not merely any step with a matching key.
        bound_stage_id = source.get("sub_plan_stage_id")
        if bound_stage_id is None:
            raise ProductionTaskError("生产轮次未绑定策略步骤")
        stage = self.conn.execute(
            """SELECT s.id, s.stage_key
               FROM sub_plan_stages s
               JOIN sub_plan_versions v ON v.id=s.sub_plan_version_id
               WHERE s.id=? AND s.sub_plan_version_id=? AND v.sub_plan_id=?""",
            (bound_stage_id, int(sub_plan_version_id), str(sub_plan_id)),
        ).fetchone()
        if stage is None or not stage["stage_key"] or str(stage["stage_key"]) != str(strategy_step_id):
            raise ProductionTaskError("策略步骤与生产轮次绑定的阶段不一致")
        plan = self.conn.execute(
            "SELECT platform FROM sub_plans WHERE id=?", (str(sub_plan_id),)
        ).fetchone()
        if plan is None or str(plan["platform"]) != str(platform):
            raise ProductionTaskError("生产任务平台与子计划平台不一致")

        existing = self.conn.execute(
            "SELECT * FROM production_tasks WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            if (
                row["round_id"] != int(round_id)
                or row["master_plan_version_id"] != int(master_plan_version_id)
                or row["sub_plan_id"] != str(sub_plan_id)
                or row["sub_plan_version_id"] != int(sub_plan_version_id)
                or row["strategy_step_id"] != str(strategy_step_id)
                or row["platform"] != str(platform)
                or row["task_ordinal"] != int(task_ordinal)
            ):
                raise ProductionTaskError("幂等键已被不同生产任务占用")
            return row

        snapshot = {
            "round": {
                "id": int(round_id),
                "master_plan_version_id": int(master_plan_version_id),
                "sub_plan_id": str(sub_plan_id),
                "sub_plan_version_id": int(sub_plan_version_id),
                "sub_plan_stage_id": source.get("sub_plan_stage_id"),
                "sub_plan_snapshot": source.get("sub_plan_snapshot"),
                "sub_plan_stage_snapshot": source.get("sub_plan_stage_snapshot"),
            },
            "task_input": input_snapshot or {},
        }
        task_id = "pt-" + uuid.uuid4().hex
        now = _stamp()
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """INSERT INTO production_tasks(
                    id,idempotency_key,round_id,master_plan_version_id,sub_plan_id,
                    sub_plan_version_id,strategy_step_id,platform,task_ordinal,
                    status,attempt_count,max_attempts,input_snapshot_json,
                    available_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'pending',0,?,?,?,?,?)""",
                (
                    task_id, idempotency_key, int(round_id),
                    int(master_plan_version_id), str(sub_plan_id),
                    int(sub_plan_version_id), str(strategy_step_id), str(platform),
                    int(task_ordinal), int(max_attempts),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    available_at or now, now, now,
                ),
            )
            if owns_transaction:
                self.conn.commit()
        except sqlite3.IntegrityError:
            if owns_transaction:
                self.conn.rollback()
            existing = self.conn.execute(
                "SELECT * FROM production_tasks WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is None:
                raise
            row = dict(existing)
            if (
                row["round_id"] != int(round_id)
                or row["master_plan_version_id"] != int(master_plan_version_id)
                or row["sub_plan_id"] != str(sub_plan_id)
                or row["sub_plan_version_id"] != int(sub_plan_version_id)
                or row["strategy_step_id"] != str(strategy_step_id)
                or row["platform"] != str(platform)
                or row["task_ordinal"] != int(task_ordinal)
            ):
                raise ProductionTaskError("幂等键已被不同生产任务占用")
            return row
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM production_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise ProductionTaskError("生产任务不存在")
        result = dict(row)
        result["input_snapshot"] = json.loads(result.pop("input_snapshot_json") or "{}")
        return result

    def list_tasks(
        self, *, round_id: int | None = None, strategy_step_id: str | None = None,
        status: str | None = None, limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        for column, value in (
            ("round_id", round_id), ("strategy_step_id", strategy_step_id),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            "SELECT id FROM production_tasks" + where
            + " ORDER BY created_at,id LIMIT ? OFFSET ?",
            [*args, max(1, min(int(limit), 500)), max(0, int(offset))],
        ).fetchall()
        return [self.get_task(row["id"]) for row in rows]

    def claim_task(
        self, task_id: str, *, worker_id: str, lease_seconds: int = 900,
    ) -> dict[str, Any]:
        if not worker_id:
            raise ProductionTaskError("worker_id 不能为空")
        now = _now()
        now_text = _stamp(now)
        expires = _stamp(now + timedelta(seconds=max(30, int(lease_seconds))))
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM production_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ProductionTaskError("生产任务不存在")
            if row["status"] != "pending":
                raise ProductionTaskError("只有 pending 任务可以领取")
            if row["available_at"] and row["available_at"] > now_text:
                raise ProductionTaskError("任务尚未到可执行时间")
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                self.conn.execute(
                    """UPDATE production_tasks SET status='failed',
                       error_code='max_attempts_exceeded',
                       error_message='任务已达到最大尝试次数',updated_at=? WHERE id=?""",
                    (now_text, task_id),
                )
                if owns_transaction:
                    self.conn.commit()
                raise ProductionTaskError("任务已达到最大尝试次数")
            attempt_no = int(row["attempt_count"]) + 1
            self.conn.execute(
                """UPDATE production_tasks SET status='running',attempt_count=?,
                   started_at=?,finished_at=NULL,lease_expires_at=?,updated_at=?
                   WHERE id=? AND status='pending'""",
                (attempt_no, now_text, expires, now_text, task_id),
            )
            self.conn.execute(
                """INSERT INTO production_task_attempts(
                    task_id,attempt_no,status,worker_id,started_at
                ) VALUES(?,?,'running',?,?)""",
                (task_id, attempt_no, worker_id, now_text),
            )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction and self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_task(task_id)

    def get_content_for_task(self, task_id: str) -> dict[str, Any] | None:
        """Find an artifact already inserted for this task before a worker crash."""
        row = self.conn.execute(
            "SELECT * FROM contents WHERE production_task_id=? LIMIT 1", (task_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def complete_task(self, task_id: str, *, content_id: str) -> dict[str, Any]:
        if not content_id:
            raise ProductionTaskError("成功任务必须关联内容成品")
        now = _stamp()
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status,attempt_count,round_id,platform FROM production_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ProductionTaskError("生产任务不存在")
            if row["status"] != "running":
                raise ProductionTaskError("只有 running 任务可以标记成功")
            content = self.conn.execute(
                "SELECT id,production_task_id,round_id,platform FROM contents WHERE id=?",
                (content_id,),
            ).fetchone()
            if content is None:
                raise ProductionTaskError("内容成品不存在")
            if int(content["round_id"]) != int(row["round_id"]):
                raise ProductionTaskError("内容成品所属轮次与生产任务不一致")
            if str(content["platform"]) != str(row["platform"]):
                raise ProductionTaskError("内容成品平台与生产任务不一致")
            if content["production_task_id"] not in (None, task_id):
                raise ProductionTaskError("内容成品已关联其他生产任务")
            self.conn.execute(
                "UPDATE contents SET production_task_id=? WHERE id=?",
                (task_id, content_id),
            )
            self.conn.execute(
                """UPDATE production_tasks SET status='succeeded',content_id=?,
                   error_code=NULL,error_message=NULL,finished_at=?,
                   lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (content_id, now, now, task_id),
            )
            self.conn.execute(
                """UPDATE production_task_attempts SET status='succeeded',
                   finished_at=?,output_content_id=? WHERE task_id=? AND attempt_no=?""",
                (now, content_id, task_id, int(row["attempt_count"])),
            )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise
        return self.get_task(task_id)

    def fail_task(
        self, task_id: str, *, error_code: str, error_message: str,
        retryable: bool = True, retry_delay_seconds: int = 0,
    ) -> dict[str, Any]:
        now_dt = _now()
        now = _stamp(now_dt)
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status,attempt_count,max_attempts FROM production_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ProductionTaskError("生产任务不存在")
            if row["status"] != "running":
                raise ProductionTaskError("只有 running 任务可以标记失败")
            can_retry = retryable and int(row["attempt_count"]) < int(row["max_attempts"])
            status = "pending" if can_retry else "failed"
            available = _stamp(now_dt + timedelta(seconds=max(0, int(retry_delay_seconds)))) if can_retry else None
            safe_message = str(error_message or "生产任务失败")[:1000]
            self.conn.execute(
                """UPDATE production_tasks SET status=?,error_code=?,error_message=?,
                   available_at=?,finished_at=?,lease_expires_at=NULL,updated_at=?
                   WHERE id=?""",
                (status, str(error_code or "production_failed")[:100], safe_message,
                 available, None if can_retry else now, now, task_id),
            )
            self.conn.execute(
                """UPDATE production_task_attempts SET status='failed',finished_at=?,
                   error_code=?,error_message=? WHERE task_id=? AND attempt_no=?""",
                (now, str(error_code or "production_failed")[:100], safe_message,
                 task_id, int(row["attempt_count"])),
            )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise
        return self.get_task(task_id)

    def cancel_task(self, task_id: str, *, reason: str = "用户取消") -> dict[str, Any]:
        now = _stamp()
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status,attempt_count FROM production_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ProductionTaskError("生产任务不存在")
            if row["status"] in {"succeeded", "cancelled"}:
                raise ProductionTaskError("已完成或已取消的任务不能再次取消")
            self.conn.execute(
                """UPDATE production_tasks SET status='cancelled',error_code='cancelled',
                   error_message=?,finished_at=?,lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (str(reason or "用户取消")[:1000], now, now, task_id),
            )
            if row["status"] == "running":
                self.conn.execute(
                    """UPDATE production_task_attempts SET status='cancelled',finished_at=?,
                       error_code='cancelled',error_message=?
                       WHERE task_id=? AND attempt_no=? AND status='running'""",
                    (now, str(reason or "用户取消")[:1000], task_id, int(row["attempt_count"])),
                )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise
        return self.get_task(task_id)

    def retry_task(
        self, task_id: str, *, retry_failed: bool = True,
        retry_delay_seconds: int = 0,
    ) -> dict[str, Any]:
        now_dt = _now()
        now = _stamp(now_dt)
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status,attempt_count,max_attempts FROM production_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ProductionTaskError("生产任务不存在")
            if row["status"] not in {"failed", "pending"} or not retry_failed:
                raise ProductionTaskError("只有失败或待执行任务可以重试")
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                raise ProductionTaskError("任务已达到最大尝试次数，需显式扩展 max_attempts")
            self.conn.execute(
                """UPDATE production_tasks SET status='pending',available_at=?,
                   error_code=NULL,error_message=NULL,finished_at=NULL,
                   lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (_stamp(now_dt + timedelta(seconds=max(0, int(retry_delay_seconds)))), now, task_id),
            )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise
        return self.get_task(task_id)

    def recover_expired_tasks(
        self, *, now: str | None = None, round_id: int | None = None,
    ) -> list[str]:
        """Requeue expired leases; attempt history remains marked abandoned."""
        current = now or _stamp()
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            query = """SELECT id,attempt_count,max_attempts FROM production_tasks
                       WHERE status='running' AND lease_expires_at IS NOT NULL
                         AND lease_expires_at<=?"""
            args: list[Any] = [current]
            if round_id is not None:
                query += " AND round_id=?"
                args.append(int(round_id))
            rows = self.conn.execute(query, args).fetchall()
            recovered: list[str] = []
            for row in rows:
                exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
                status = "failed" if exhausted else "pending"
                self.conn.execute(
                    """UPDATE production_tasks SET status=?,error_code='worker_lease_expired',
                       error_message='执行器租约过期，任务已恢复',finished_at=?,
                       lease_expires_at=NULL,updated_at=? WHERE id=?""",
                    (status, current if exhausted else None, current, row["id"]),
                )
                self.conn.execute(
                    """UPDATE production_task_attempts SET status='abandoned',finished_at=?,
                       error_code='worker_lease_expired',
                       error_message='执行器租约过期，任务已恢复'
                       WHERE task_id=? AND attempt_no=? AND status='running'""",
                    (current, row["id"], int(row["attempt_count"])),
                )
                recovered.append(row["id"])
            if owns_transaction:
                self.conn.commit()
            return recovered
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def create_tasks_for_step(
        self, *, round_id: int, master_plan_version_id: int, sub_plan_id: str,
        sub_plan_version_id: int, strategy_step_id: str, tasks: list[dict[str, Any]],
        max_attempts: int = 3,
    ) -> list[dict[str, Any]]:
        """Create multiple independent items for one strategy step.

        Each item needs a platform and a deterministic idempotency_key. Replaying
        the same batch is safe; already-created task identities are returned.
        """
        if not isinstance(tasks, list) or not tasks:
            raise ProductionTaskError("策略步骤至少需要一项生产任务")
        created = []
        for index, spec in enumerate(tasks, start=1):
            if not isinstance(spec, dict):
                raise ProductionTaskError("生产任务定义必须是对象")
            created.append(self.create_task(
                round_id=round_id,
                master_plan_version_id=master_plan_version_id,
                sub_plan_id=sub_plan_id,
                sub_plan_version_id=sub_plan_version_id,
                strategy_step_id=strategy_step_id,
                platform=str(spec.get("platform") or ""),
                task_ordinal=int(spec.get("task_ordinal") or index),
                idempotency_key=str(spec.get("idempotency_key") or ""),
                input_snapshot=spec.get("input_snapshot") or {},
                max_attempts=max_attempts,
                available_at=spec.get("available_at"),
            ))
        return created

    def extend_attempts(self, task_id: str, *, max_attempts: int) -> dict[str, Any]:
        """Explicitly raise a task's retry ceiling without erasing attempt history."""
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status,attempt_count,max_attempts FROM production_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ProductionTaskError("生产任务不存在")
            if row["status"] not in {"failed", "pending"}:
                raise ProductionTaskError("只有失败或待执行任务可以调整最大尝试次数")
            if int(max_attempts) <= int(row["max_attempts"]) or int(max_attempts) <= int(row["attempt_count"]):
                raise ProductionTaskError("新的最大尝试次数必须高于当前上限和已尝试次数")
            self.conn.execute(
                "UPDATE production_tasks SET max_attempts=?,updated_at=? WHERE id=?",
                (int(max_attempts), _stamp(), task_id),
            )
            if owns_transaction:
                self.conn.commit()
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise
        return self.get_task(task_id)

    def round_progress(self, round_id: int) -> dict[str, int]:
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
                      SUM(CASE WHEN status IN ('pending','running') THEN 1 ELSE 0 END) AS active
               FROM production_tasks WHERE round_id=?""",
            (round_id,),
        ).fetchone()
        return {key: int(row[key] or 0) for key in ("total","succeeded","failed","cancelled","active")}

    def step_progress(
        self, strategy_step_id: str, *, sub_plan_id: str | None = None,
        sub_plan_version_id: int | None = None,
    ) -> dict[str, int]:
        """Aggregate task execution for one stable strategy key and optional immutable version."""
        clauses = ["strategy_step_id=?"]
        args: list[Any] = [strategy_step_id]
        if sub_plan_id is not None:
            clauses.append("sub_plan_id=?")
            args.append(str(sub_plan_id))
        if sub_plan_version_id is not None:
            clauses.append("sub_plan_version_id=?")
            args.append(int(sub_plan_version_id))
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
                      SUM(CASE WHEN status IN ('pending','running') THEN 1 ELSE 0 END) AS active
               FROM production_tasks WHERE """ + " AND ".join(clauses),
            args,
        ).fetchone()
        return {key: int(row[key] or 0) for key in ("total","succeeded","failed","cancelled","active")}
