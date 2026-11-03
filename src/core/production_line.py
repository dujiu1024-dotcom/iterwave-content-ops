"""Task-isolated runner for the content production line.

The runner executes durable tasks after their strategy-step provenance has been
validated by ProductionTaskStore. A single task failure is recorded and never
aborts sibling tasks.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .production_task_store import ProductionTaskError, ProductionTaskStore

logger = logging.getLogger(__name__)

Producer = Callable[[dict[str, Any]], str | dict[str, Any] | Awaitable[str | dict[str, Any]]]


class ProductionLineRunner:
    def __init__(self, store: ProductionTaskStore, *, worker_id: str):
        if not worker_id:
            raise ValueError("worker_id 不能为空")
        self.store = store
        self.worker_id = worker_id

    async def execute_task(self, task_id: str, producer: Producer) -> dict[str, Any]:
        """Claim and execute one task; recover an artifact already persisted before a crash."""
        try:
            task = self.store.claim_task(task_id, worker_id=self.worker_id, lease_seconds=3600)
        except ProductionTaskError:
            return self.store.get_task(task_id)
        try:
            existing = self.store.get_content_for_task(task_id)
            if existing is not None:
                return self.store.complete_task(task_id, content_id=str(existing["id"]))

            result = producer(task)
            if inspect.isawaitable(result):
                result = await result
            content_id = result.get("content_id") if isinstance(result, dict) else result
            if not content_id:
                raise ProductionTaskError("生产器未返回内容成品 ID")
            return self.store.complete_task(task_id, content_id=str(content_id))
        except asyncio.CancelledError:
            self.store.cancel_task(task_id, reason="生产执行协程被取消")
            raise
        except Exception as exc:  # one task's failure must not escape into its siblings
            logger.exception("production task failed id=%s", task_id)
            code = str(getattr(exc, "code", "production_failed"))
            retryable = bool(getattr(exc, "retryable", True))
            try:
                return self.store.fail_task(
                    task_id,
                    error_code=code,
                    error_message=str(exc) or "生产任务失败",
                    retryable=retryable,
                )
            except ProductionTaskError:
                # A concurrent cancellation or terminal transition won the race.
                return self.store.get_task(task_id)

    async def run_batch(self, task_ids: list[str], producer: Producer) -> dict[str, Any]:
        """Run tasks independently and return partial-success accounting."""
        results = []
        # A repeated ID is a caller replay, not a second unit of work.
        for task_id in dict.fromkeys(task_ids):
            results.append(await self.execute_task(task_id, producer))
        return {
            "total": len(results),
            "succeeded": sum(row["status"] == "succeeded" for row in results),
            "failed": sum(row["status"] == "failed" for row in results),
            "pending": sum(row["status"] == "pending" for row in results),
            "cancelled": sum(row["status"] == "cancelled" for row in results),
            "results": results,
        }
