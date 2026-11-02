"""全局任务队列消费者。

消费者独立于前台活动账号组。每个任务按自身 tenant_id 构造运行时，并在异步上下文中
绑定对应登录态目录。生产轮次与账号组定时作业统一由全局单消费者执行。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.core.task_queue import GlobalTaskQueue, QueuedTask

logger = logging.getLogger(__name__)


class WorkerLeaseLost(RuntimeError):
    """消费者无法确认自己仍持有全局任务租约。"""


class TaskExecutionFailed(RuntimeError):
    """业务执行器返回结构化失败报告，队列任务必须进入 failed。"""

    code = "task_execution_failed"


class GlobalTaskWorker:
    def __init__(
        self,
        queue: GlobalTaskQueue,
        build_tenant_context: Callable[[str], dict[str, Any]],
        *,
        poll_interval_seconds: float = 0.5,
        lease_seconds: int = 90,
        heartbeat_seconds: float = 20,
        shutdown_grace_seconds: float | None = None,
    ):
        self.queue = queue
        self.build_tenant_context = build_tenant_context
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        if shutdown_grace_seconds is None:
            try:
                shutdown_grace_seconds = float(os.getenv("ITERWAVE_TASK_SHUTDOWN_GRACE_SECONDS", "30"))
            except (TypeError, ValueError):
                shutdown_grace_seconds = 30.0
        self.shutdown_grace_seconds = max(0.0, min(float(shutdown_grace_seconds), 300.0))
        self.owner = uuid.uuid4().hex
        self._reconciled_interrupted_task_ids: set[str] = set()

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info("全局任务消费者启动 worker=%s", self.owner[:8])
        self._sync_terminal_rounds()
        try:
            while not stop_event.is_set():
                try:
                    self.queue.recover_expired()
                    self._sync_terminal_rounds()
                    task = self.queue.claim_next(self.owner, lease_seconds=self.lease_seconds)
                except Exception:
                    logger.exception("全局任务队列读取失败")
                    await self._wait(stop_event, self.poll_interval_seconds * 4)
                    continue
                if task is None:
                    await self._wait(stop_event, self.poll_interval_seconds)
                    continue
                await self._execute(task, stop_event=stop_event)
        finally:
            self.queue.release_worker(self.owner)
            logger.info("全局任务消费者退出 worker=%s", self.owner[:8])

    def _sync_terminal_rounds(self) -> None:
        """收口全部分页中的已中断轮次，避免历史记录超过 500 条后永久漏对账。"""
        page = 1
        page_size = 500
        while True:
            try:
                tasks, total = self.queue.list_page(
                    scope="failed", page=page, page_size=page_size,
                )
            except Exception:
                logger.exception("读取终态任务失败 page=%s", page)
                return
            for task in tasks:
                if task.status != "interrupted" or task.task_type != "round_run":
                    continue
                if task.task_id in self._reconciled_interrupted_task_ids:
                    continue
                reconciled = self._reconcile_round_failure(
                    task, task.error_message or task.error_code or "任务已中断",
                )
                if reconciled:
                    self._reconciled_interrupted_task_ids.add(task.task_id)
            if not tasks or page * page_size >= total:
                break
            page += 1
    async def _wait(self, stop_event: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _heartbeat_loop(
        self, task: QueuedTask, stop_event: threading.Event,
        loop: asyncio.AbstractEventLoop, lease_lost: asyncio.Event,
    ) -> None:
        """在线程中续租，避免同步 I/O 阻塞事件循环时心跳也停止。"""
        while not stop_event.wait(self.heartbeat_seconds):
            try:
                renewed = self.queue.renew(
                    task.task_id, self.owner, lease_seconds=self.lease_seconds,
                )
            except Exception:
                logger.exception("任务租约续期异常 task_id=%s", task.task_id)
                loop.call_soon_threadsafe(lease_lost.set)
                return
            if not renewed:
                logger.error("任务租约续期失败，停止执行 task_id=%s", task.task_id)
                loop.call_soon_threadsafe(lease_lost.set)
                return

    async def _run_task(self, task: QueuedTask) -> dict[str, Any]:
        allowed_scheduler_methods = {
            "_job_publish_polling", "_job_data_fetch", "_job_daily_ops",
            "_job_comment_polling", "_job_cb_recovery", "_job_hot_topics_refresh", "_job_kb_scan",
            "_job_kb_sync", "_job_data_retention_cleanup",
            "_job_round_timeout_sweeper", "_job_round_review", "_job_autogrow_next_round",
        }
        if task.task_type not in {"round_run", "scheduler_job"}:
            raise ValueError(f"不支持的任务类型：{task.task_type}")
        context = self.build_tenant_context(task.tenant_id)
        from src.core import login_manager
        from src.core.notify import bind_context_notifier
        from src.core.task_context import bind_config

        data_dir = Path(context["cfg"].resolve_path(context["cfg"].paths.data_dir))
        try:
            # 生产任务只允许在入队时配置与母计划版本仍一致时执行，防止排队期间配置热更新改变任务含义。
            snapshot = task.payload.get("execution_snapshot")
            if "execution_snapshot" in task.payload:
                if not isinstance(snapshot, dict):
                    from src.core.task_snapshot import StaleTaskSnapshot
                    raise StaleTaskSnapshot("任务执行快照格式无效，已拒绝执行")
                from src.core.task_snapshot import validate_execution_snapshot
                validate_execution_snapshot(snapshot, context["cfg"], context["conn"])
            # 后台通知必须与任务 tenant_id 绑定，不能读取前台切换时更新的模块级通知器。
            with (
                login_manager.tenant_login_dir(data_dir),
                bind_context_notifier(context.get("notifier")),
                bind_config(context["cfg"]),
            ):
                if task.task_type == "round_run":
                    round_id = int(task.payload["round_id"])
                    operation = task.payload.get("operation", "run")
                    feedback_data = task.payload.get("feedback")
                    feedback = None
                    if isinstance(feedback_data, dict):
                        from src.core.autogrow import StrategyFeedback
                        feedback = StrategyFeedback(**feedback_data)
                    if operation == "save_pending_drafts":
                        return await context["executor"].save_pending_drafts(round_id)
                    if operation == "run":
                        try:
                            report = await context["executor"].run(
                                round_id,
                                topic_count=task.payload.get("topic_count"),
                                platform_values=task.payload.get("platforms"),
                                save_drafts=task.payload.get("save_drafts"),
                                feedback=feedback,
                                template_data=task.payload.get("template_snapshot"),
                            )
                        except asyncio.CancelledError:
                            self._mark_content_plan_failed(context["conn"], round_id)
                            raise
                        except Exception:
                            self._mark_content_plan_failed(context["conn"], round_id)
                            raise
                        self._complete_content_plan(context["conn"], round_id, report)
                        return report
                    raise ValueError(f"不支持的轮次任务操作：{operation}")

                method = task.payload.get("method")
                args = task.payload.get("args", [])
                if method not in allowed_scheduler_methods or not isinstance(args, list):
                    raise ValueError("定时任务类型或参数无效")
                task_type = {
                    "_job_publish_polling": "publish_polling",
                    "_job_data_fetch": "data_fetch",
                    "_job_daily_ops": "daily_ops",
                    "_job_comment_polling": "comment_polling",
                    "_job_cb_recovery": "cb_recovery",
                    "_job_kb_scan": "kb_scan",
                    "_job_kb_sync": "kb_scan",
                    "_job_hot_topics_refresh": "hot_topics_refresh",
                    "_job_data_retention_cleanup": "data_retention_cleanup",
                    "_job_round_timeout_sweeper": "round_pipeline",
                    "_job_round_review": "round_pipeline",
                    "_job_autogrow_next_round": "round_pipeline",
                }[method]
                task_run_id = self._start_scheduler_task_run(
                    context["conn"], context["cfg"], task_type, method, args,
                )
                from src.core.task_context import bind_task_context
                try:
                    with bind_task_context(task_run_id):
                        result = await getattr(context["scheduler"], method)(*args)
                except Exception:
                    logger.exception(
                        "定时任务执行异常 tenant=%s method=%s", task.tenant_id, method,
                    )
                    self._finish_scheduler_task_run(
                        context["conn"], context["cfg"], task_run_id, "failed",
                        "定时任务执行异常，请检查服务端运行日志",
                    )
                    raise
                else:
                    failed_report = isinstance(result, dict) and result.get("final_status") == "failed"
                    errors = result.get("errors") if isinstance(result, dict) else None
                    error_msg = "；".join(str(item) for item in errors if item) if isinstance(errors, list) else None
                    self._finish_scheduler_task_run(
                        context["conn"], context["cfg"], task_run_id,
                        "failed" if failed_report else "success",
                        (error_msg or "调度任务返回 failed") if failed_report else None,
                    )
                return result if isinstance(result, dict) else {"completed": True}
        finally:
            scheduler = context.get("scheduler")
            if scheduler is not None:
                try:
                    scheduler.shutdown()
                except Exception:
                    logger.exception("关闭任务账号组调度器失败")
            conn = context.get("conn")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("关闭任务账号组数据库连接失败")


    @staticmethod
    def _start_scheduler_task_run(conn, cfg, task_type: str, method: str, args: list) -> int | None:
        """为全局队列中的定时任务建立本账号组 task_run，供日志和通知精确关联。"""
        if conn is None or not callable(getattr(conn, "execute", None)):
            return None
        import json
        from src.core.timezone import business_now
        try:
            now = business_now(cfg).isoformat(timespec="seconds")
            identity = json.dumps(args, ensure_ascii=False, default=str)[:300]
            cur = conn.execute(
                "INSERT INTO task_runs (task_type, biz_id, status, attempt, started_at, created_at) "
                "VALUES (?, ?, 'running', 1, ?, ?)",
                (task_type, f"scheduler:{method}:{identity}", now, now),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception:
            logger.exception("创建定时任务审计记录失败 method=%s", method)
            return None

    @staticmethod
    def _finish_scheduler_task_run(conn, cfg, task_run_id: int | None, status: str, error_msg: str | None) -> None:
        if conn is None or task_run_id is None:
            return
        from src.core.timezone import business_now
        try:
            conn.execute(
                "UPDATE task_runs SET status=?, finished_at=?, error_msg=? WHERE id=? AND status='running'",
                (status, business_now(cfg).isoformat(timespec="seconds"), error_msg, task_run_id),
            )
            conn.commit()
        except Exception:
            logger.exception("结束定时任务审计记录失败 task_run_id=%s", task_run_id)

    @staticmethod
    def _mark_round_failed(conn, round_id: int, reason: str, cfg=None) -> bool:
        """兜底收口 Worker 捕获到的执行器外异常，避免轮次永久停在 producing。"""
        if conn is None or not callable(getattr(conn, "execute", None)):
            return False
        from src.core.state_machine import RoundStateMachine

        row = conn.execute(
            "SELECT status FROM spiral_rounds WHERE id=?", (round_id,),
        ).fetchone()
        if row is None or row["status"] not in {"producing", "analyzing"}:
            return False
        return RoundStateMachine(conn, cfg=cfg).transition(
            round_id, "failed", f"Worker 异常兜底收口：{str(reason)[:300]}",
        )

    def _reconcile_round_failure(self, task: QueuedTask, reason: str) -> bool:
        """重新构建任务所属账号组上下文，对账轮次和内容计划状态。

        返回是否完成对账；临时数据库/上下文故障时允许后续轮询重试。
        """
        if task.task_type != "round_run":
            return True
        try:
            round_id = int(task.payload["round_id"])
            context = self.build_tenant_context(task.tenant_id)
        except Exception:
            logger.exception(
                "Worker 失败后无法重建账号组上下文 task_id=%s tenant_id=%s",
                task.task_id, task.tenant_id,
            )
            return False
        reconciled = False
        try:
            conn = context.get("conn")
            self._mark_round_failed(conn, round_id, reason, cfg=context.get("cfg"))
            self._mark_content_plan_failed(conn, round_id)
            reconciled = True
        except Exception:
            logger.exception(
                "Worker 失败状态对账异常 task_id=%s tenant_id=%s round_id=%s",
                task.task_id, task.tenant_id, task.payload.get("round_id"),
            )
        finally:
            scheduler = context.get("scheduler")
            if scheduler is not None:
                try:
                    scheduler.shutdown()
                except Exception:
                    logger.exception("关闭失败对账调度器异常")
            conn = context.get("conn")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("关闭失败对账数据库连接异常")
        return reconciled

    @staticmethod
    def _mark_content_plan_failed(conn, round_id: int) -> None:
        """生产异常时将关联计划标记失败，允许用户修复后重试。"""
        if conn is None or not callable(getattr(conn, "execute", None)):
            return
        relation = conn.execute(
            "SELECT plan_id FROM content_plan_rounds WHERE round_id=?", (round_id,),
        ).fetchone()
        if relation is None:
            return
        conn.execute(
            "UPDATE content_plans SET status='failed',version=version+1,updated_at=? "
            "WHERE id=? AND status='generating'",
            (datetime.now().astimezone().isoformat(timespec="seconds"), relation["plan_id"]),
        )
        conn.commit()

    @staticmethod
    def _complete_content_plan(conn, round_id: int, report: Any) -> None:
        """生产完成后把轮次产出的草稿关联回计划，并推进计划状态。"""
        if conn is None or not callable(getattr(conn, "execute", None)):
            return
        relation = conn.execute(
            "SELECT plan_id FROM content_plan_rounds WHERE round_id=?", (round_id,),
        ).fetchone()
        if relation is None:
            return
        plan_id = relation["plan_id"]
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        drafts = conn.execute(
            "SELECT d.id FROM drafts d JOIN contents c ON c.id=d.content_id WHERE c.round_id=?",
            (round_id,),
        ).fetchall()
        for draft in drafts:
            conn.execute(
                "INSERT OR IGNORE INTO content_plan_drafts(plan_id,draft_id,round_id,created_at) VALUES(?,?,?,?)",
                (plan_id, draft["id"], round_id, now),
            )
        failed = isinstance(report, dict) and report.get("final_status") == "failed"
        status = "failed" if failed else "generated"
        conn.execute(
            "UPDATE content_plans SET status=?,version=version+1,updated_at=? "
            "WHERE id=? AND status='generating'",
            (status, now, plan_id),
        )
        conn.commit()

    async def _execute(
        self, task: QueuedTask, *, stop_event: asyncio.Event | None = None,
    ) -> None:
        lease_lost = asyncio.Event()
        heartbeat_stop = threading.Event()
        loop = asyncio.get_running_loop()
        heartbeat = asyncio.create_task(asyncio.to_thread(
            self._heartbeat_loop, task, heartbeat_stop, loop, lease_lost,
        ))
        execution = asyncio.create_task(self._run_task(task))
        stop_waiter = asyncio.create_task(stop_event.wait()) if stop_event is not None else None
        try:
            wait_for = {execution, heartbeat}
            if stop_waiter is not None:
                wait_for.add(stop_waiter)
            done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

            if stop_waiter is not None and stop_waiter in done and not execution.done():
                completed, _ = await asyncio.wait(
                    {execution}, timeout=self.shutdown_grace_seconds,
                )
                if not completed:
                    execution.cancel()
                    try:
                        await execution
                    except asyncio.CancelledError:
                        pass
                    heartbeat_stop.set()
                    await heartbeat
                    if not self.queue.interrupt(task.task_id, self.owner):
                        raise WorkerLeaseLost("停机时任务租约已失效，无法安全写入 interrupted")
                    logger.warning(
                        "服务停止超出宽限期，任务已中断 task_id=%s tenant_id=%s",
                        task.task_id, task.tenant_id,
                    )
                    return

            if heartbeat in done and not execution.done():
                execution.cancel()
                try:
                    await execution
                except asyncio.CancelledError:
                    pass
                raise WorkerLeaseLost("任务租约丢失，已请求停止当前任务")
            report = await execution
            if lease_lost.is_set():
                raise WorkerLeaseLost("任务租约丢失，拒绝提交完成状态")
            if isinstance(report, dict) and report.get("final_status") == "failed":
                errors = report.get("errors")
                detail = "；".join(str(item) for item in errors if item) if isinstance(errors, list) else ""
                raise TaskExecutionFailed(detail or "业务执行器返回 failed")
            if not self.queue.finish(task.task_id, self.owner, result=report):
                raise WorkerLeaseLost("任务完成状态写入失败，租约可能已失效")
        except WorkerLeaseLost:
            logger.error(
                "任务因租约丢失停止 task_id=%s tenant_id=%s",
                task.task_id, task.tenant_id,
            )
        except asyncio.CancelledError:
            execution.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 — 单任务失败不得终止消费者
            from src.core.redact import redact
            error_code = redact(str(getattr(exc, "code", "round_execution_failed")))[:80]
            # 任务历史保留可诊断的业务错误；先脱敏并隐藏本机路径，不写 traceback / 请求头。
            import re
            detail = redact(str(exc)).strip()
            detail = re.sub(r"(?<!:)/(?:[^\s:'\"<>]+/)*[^\s:'\"<>]+", "[路径已隐藏]", detail)
            safe_message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
            safe_message = safe_message[:500]
            logger.error(
                "全局队列任务执行失败 task_id=%s tenant_id=%s error=%s",
                task.task_id, task.tenant_id, safe_message,
            )
            try:
                failed = self.queue.fail(
                    task.task_id, self.owner,
                    error_code=error_code, error_message=safe_message,
                )
            except Exception:
                logger.exception("写入任务失败状态异常 task_id=%s", task.task_id)
                failed = False
            # 只有当前 Worker 确实提交了队列失败状态，才有权对账轮次；
            # 租约已丢失时，不能覆盖新 Worker 的执行结果。
            if failed:
                self._reconcile_round_failure(task, safe_message)
            else:
                logger.warning(
                    "任务失败状态未由当前租约持有者提交，跳过轮次对账 task_id=%s",
                    task.task_id,
                )
        finally:
            if not execution.done():
                execution.cancel()
                try:
                    await execution
                except asyncio.CancelledError:
                    pass
            heartbeat_stop.set()
            if not heartbeat.done():
                await heartbeat
            if stop_waiter is not None:
                stop_waiter.cancel()
                try:
                    await stop_waiter
                except asyncio.CancelledError:
                    pass
