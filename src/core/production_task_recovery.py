"""Crash-safe reconciliation and explicit recovery for persistent production tasks.

The production pipeline is sequential per round. When it raises, only still-running
tasks in that round are reconciled; already-succeeded sibling tasks are never reset.
A persisted content row is authoritative and is adopted instead of regenerated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .production_task_store import ProductionTaskError, ProductionTaskStore

logger = logging.getLogger(__name__)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reconcile_round_tasks(
    conn,
    *,
    round_id: int,
    interrupted: bool,
    error: Exception | None = None,
) -> list[dict[str, Any]]:
    """Close running tasks for a known-finished executor, adopting persisted artifacts.

    interrupted=True means this worker has definitively exited by exception, so its
    active lease is no longer live. interrupted=False only adopts artifacts and does
    not fail tasks that might still be owned by another worker.
    """
    store = ProductionTaskStore(conn)
    rows = conn.execute(
        "SELECT id FROM production_tasks WHERE round_id=? AND status='running' ORDER BY created_at,id",
        (int(round_id),),
    ).fetchall()
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        task_id = str(row["id"])
        try:
            artifact = store.get_content_for_task(task_id)
            if artifact is not None:
                store.complete_task(task_id, content_id=str(artifact["id"]))
                outcomes.append({"task_id": task_id, "action": "adopted_content", "content_id": str(artifact["id"])})
                continue
            if not interrupted:
                continue

            # Only an explicitly retryable model-generation failure is safe to replay.
            # Image-provider, filesystem, OCR, DB and unknown failures are conservative:
            # the operator must inspect side effects before explicitly retrying.
            from .content_pipeline import ContentGenerationError
            retryable = isinstance(error, ContentGenerationError) and bool(error.retryable)
            code = (
                str(getattr(error, "code", "content_generation_retryable"))
                if retryable else "interrupted_requires_review"
            )
            message = (
                str(error) if error is not None
                else "执行器异常退出；未找到已落库成品，请核实图片/API/外部副作用后再恢复"
            )
            store.fail_task(
                task_id,
                error_code=code,
                error_message=message[:1000],
                retryable=retryable,
            )
            outcomes.append({
                "task_id": task_id,
                "action": "retry_scheduled" if retryable else "manual_review_required",
                "error_code": code,
            })
        except Exception:
            logger.exception("生产任务收尾失败 task=%s round=%s", task_id, round_id)
            outcomes.append({"task_id": task_id, "action": "reconciliation_failed"})
    return outcomes


def reconcile_expired_production_tasks(conn, *, limit: int = 200) -> list[dict[str, Any]]:
    """Global lease sweeper for historical rounds; safe to call from a scheduler."""
    now = _utc_stamp()
    rows = conn.execute(
        """SELECT id,round_id FROM production_tasks
           WHERE status='running' AND lease_expires_at IS NOT NULL
             AND lease_expires_at<=?
           ORDER BY lease_expires_at,id LIMIT ?""",
        (now, max(1, min(int(limit), 1000))),
    ).fetchall()
    store = ProductionTaskStore(conn)
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        task_id = str(row["id"])
        try:
            artifact = store.get_content_for_task(task_id)
            if artifact is not None:
                store.complete_task(task_id, content_id=str(artifact["id"]))
                outcomes.append({"task_id": task_id, "round_id": int(row["round_id"]), "action": "adopted_content"})
            else:
                store.fail_task(
                    task_id,
                    error_code="lease_expired_requires_review",
                    error_message=(
                        "生产任务租约已过期且未找到已落库成品；可能存在未确认的图片/API副作用，"
                        "需人工核实后才能显式重试"
                    ),
                    retryable=False,
                )
                outcomes.append({
                    "task_id": task_id,
                    "round_id": int(row["round_id"]),
                    "action": "manual_review_required",
                })
        except ProductionTaskError:
            # A concurrent worker may have completed/claimed the task after selection.
            logger.info("跳过已并发迁移的过期生产任务 task=%s", task_id)
        except Exception:
            logger.exception("历史生产任务租约恢复失败 task=%s", task_id)
            outcomes.append({"task_id": task_id, "round_id": int(row["round_id"]), "action": "reconciliation_failed"})
    return outcomes


def recover_production_task(
    conn,
    task_id: str,
    *,
    confirmed_no_external_side_effect: bool = False,
) -> dict[str, Any]:
    """Explicit task-level recovery; never regenerates when an artifact already exists."""
    store = ProductionTaskStore(conn)
    task = store.get_task(task_id)
    artifact = store.get_content_for_task(task_id)
    if artifact is not None:
        if task["status"] == "succeeded":
            return {"task": task, "content_id": str(artifact["id"]), "action": "already_succeeded"}
        if task["status"] == "running":
            completed = store.complete_task(task_id, content_id=str(artifact["id"]))
            return {"task": completed, "content_id": str(artifact["id"]), "action": "adopted_content"}
        if task["status"] in {"failed", "pending"}:
            # Move through the normal claim transition to preserve attempt history.
            if task["status"] == "failed":
                if int(task["attempt_count"]) >= int(task["max_attempts"]):
                    store.extend_attempts(task_id, max_attempts=int(task["attempt_count"]) + 1)
                store.retry_task(task_id, retry_failed=True)
            store.claim_task(task_id, worker_id="manual-recovery", lease_seconds=900)
            completed = store.complete_task(task_id, content_id=str(artifact["id"]))
            return {"task": completed, "content_id": str(artifact["id"]), "action": "adopted_content"}

    if task["status"] == "succeeded":
        raise ProductionTaskError("成功任务缺少关联成品，禁止重试；需先修复数据关联")
    if task["status"] == "running":
        raise ProductionTaskError("任务仍在运行或租约未完成，不能人工重试")
    if task["error_code"] in {"interrupted_requires_review", "lease_expired_requires_review"} and not confirmed_no_external_side_effect:
        raise ProductionTaskError("该任务需要人工核实；确认没有未完成的外部副作用后才能重试")
    if task["status"] == "failed":
        if int(task["attempt_count"]) >= int(task["max_attempts"]):
            store.extend_attempts(task_id, max_attempts=int(task["attempt_count"]) + 1)
        store.retry_task(task_id, retry_failed=True)
    elif task["status"] != "pending":
        raise ProductionTaskError("当前任务状态不支持恢复")
    return {"task": store.get_task(task_id), "action": "ready_to_retry"}


def install_safe_expired_recovery() -> None:
    """Replace legacy blind lease requeue with artifact-aware reconciliation."""
    def _safe_recover(self, *, now: str | None = None, round_id: int | None = None) -> list[str]:
        current = now or _utc_stamp()
        query = (
            "SELECT id,round_id FROM production_tasks WHERE status='running' "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at<=?"
        )
        args: list[Any] = [current]
        if round_id is not None:
            query += " AND round_id=?"
            args.append(int(round_id))
        query += " ORDER BY lease_expires_at,id"
        rows = self.conn.execute(query, args).fetchall()
        recovered: list[str] = []
        for row in rows:
            task_id = str(row["id"])
            try:
                artifact = self.get_content_for_task(task_id)
                if artifact is not None:
                    self.complete_task(task_id, content_id=str(artifact["id"]))
                else:
                    self.fail_task(
                        task_id,
                        error_code="lease_expired_requires_review",
                        error_message=(
                            "任务租约过期且没有已落库成品；为避免重复图片/API副作用，"
                            "任务已转人工核实，不会自动重新生成"
                        ),
                        retryable=False,
                    )
                recovered.append(task_id)
            except ProductionTaskError:
                # Concurrent completion/claim wins; never overwrite its state.
                continue
        return recovered

    ProductionTaskStore.recover_expired_tasks = _safe_recover
