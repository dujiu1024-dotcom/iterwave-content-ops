"""Dedicated OS process entry point for the durable task worker.

The API process supervises this process. Keeping Playwright, synchronous adapters and
blocking database calls out of the ASGI process makes hard termination possible.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import threading
import time

from src.core.task_worker_supervisor import _positive_seconds, parent_process_alive

DEFAULT_TASK_WORKERS = 1
MAX_TASK_WORKERS = 8
MAX_SHUTDOWN_GRACE_SECONDS = 90.0


def resolve_worker_count(raw: str | None) -> int:
    """Single global FIFO consumer unless parallelism is explicitly configured."""
    if raw is None or not str(raw).strip():
        return DEFAULT_TASK_WORKERS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_TASK_WORKERS
    return max(1, min(value, MAX_TASK_WORKERS))


def resolve_shutdown_grace(raw: str | None) -> float:
    """Must match the watchdog's bound so the worker can write `interrupted` itself."""
    return _positive_seconds(raw, 30.0, maximum=MAX_SHUTDOWN_GRACE_SECONDS)


def main() -> None:
    # Start parent-loss protection before importing application modules or opening databases.
    expected_parent_pid = int(os.getenv("ITERWAVE_TASK_SUPERVISOR_PID", "0") or 0)
    expected_parent_token = os.getenv("ITERWAVE_TASK_SUPERVISOR_START_TOKEN")

    def monitor_supervisor() -> None:
        while True:
            if expected_parent_pid and not parent_process_alive(
                expected_parent_pid, expected_parent_token,
            ):
                logging.getLogger(__name__).critical(
                    "task supervisor disappeared; terminating worker process group"
                )
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    os._exit(70)
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                except ProcessLookupError:
                    os._exit(70)
                return
            time.sleep(0.5)

    threading.Thread(
        target=monitor_supervisor, name="worker-parent-watch", daemon=True,
    ).start()
    # Import lazily: importing the API module defines the runtime factory but does not
    # enter its FastAPI lifespan or start another worker.
    from src.api import main as api
    from src.core.task_queue import GlobalTaskQueue
    from src.core.task_worker import GlobalTaskWorker

    cfg = api.load_config()
    registry = api.TenantRegistry(api.PROJECT_ROOT, cfg=cfg)
    registry.initialize()
    queue = GlobalTaskQueue(registry.system_db_file)
    queue.initialize()
    api._task_queue = queue

    # 默认单消费者：全局 FIFO 严格按 queue_order 串行执行。
    # 跨账号组并行必须显式设置 ITERWAVE_TASK_WORKERS>1 开启。
    worker_count = resolve_worker_count(os.getenv("ITERWAVE_TASK_WORKERS"))
    shutdown_grace = resolve_shutdown_grace(os.getenv("ITERWAVE_TASK_SHUTDOWN_GRACE_SECONDS"))
    logging.getLogger(__name__).info(
        "task worker process starting consumers=%d mode=%s",
        worker_count, "global-fifo" if worker_count == 1 else "cross-tenant-parallel",
    )
    workers = [
        GlobalTaskWorker(
            queue,
            lambda tenant_id: api._build_tenant_runtime_context(registry, tenant_id),
            shutdown_grace_seconds=shutdown_grace,
            reconcile_terminal_rounds=(index == 0),
        )
        for index in range(worker_count)
    ]

    reconcile_samples = getattr(workers[0], "reconcile_orphaned_activation_samples", None)
    if callable(reconcile_samples):
        try:
            report = reconcile_samples(registry.list())
            if report.get("failed"):
                logging.getLogger(__name__).warning(
                    "worker startup reconciled orphaned activation samples: %s", report,
                )
        except Exception:
            logging.getLogger(__name__).exception("activation sample startup reconciliation failed")

    async def run() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            loop.call_soon_threadsafe(stop_event.set)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, request_stop)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda *_: request_stop())
        await asyncio.gather(*(worker.run(stop_event) for worker in workers))

    asyncio.run(run())


if __name__ == "__main__":
    main()
