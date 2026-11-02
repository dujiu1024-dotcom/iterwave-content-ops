"""Standalone watchdog process supervising the isolated global task worker."""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

from src.core.task_worker_supervisor import TaskWorkerSupervisor, _positive_seconds, parent_process_alive

logger = logging.getLogger(__name__)


def main() -> None:
    stopped = threading.Event()
    parent_lost = threading.Event()
    expected_parent_pid = int(os.getenv("ITERWAVE_API_PARENT_PID", "0") or 0)
    expected_parent_token = os.getenv("ITERWAVE_API_PARENT_START_TOKEN")

    def request_stop(*_args) -> None:
        stopped.set()

    def monitor_api_parent() -> None:
        while not stopped.wait(0.5):
            if expected_parent_pid and not parent_process_alive(
                expected_parent_pid, expected_parent_token,
            ):
                parent_lost.set()
                stopped.set()
                return

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)
    threading.Thread(
        target=monitor_api_parent, name="watchdog-api-parent", daemon=True,
    ).start()

    supervisor = None
    exit_code = 0
    try:
        if parent_lost.is_set():
            return
        # Import the API runtime factory only after parent-loss protection is active.
        from src.api import main as api
        from src.core.task_queue import GlobalTaskQueue

        cfg = api.load_config()
        registry = api.TenantRegistry(api.PROJECT_ROOT, cfg=cfg)
        registry.initialize()
        queue = GlobalTaskQueue(registry.system_db_file)
        queue.initialize()
        # A previous watchdog may have exited while its worker child is still observing
        # parent loss. Never clear live leases here: a replacement worker may only reclaim
        # tasks after their leases expire or after the old worker is confirmed dead.
        if stopped.wait(2.0) or parent_lost.is_set():
            return
        queue.recover_expired()

        worker_grace = _positive_seconds(
            os.getenv("ITERWAVE_TASK_SHUTDOWN_GRACE_SECONDS"), 30.0, maximum=90.0,
        )
        supervisor = TaskWorkerSupervisor(
            queue, project_root=api.PROJECT_ROOT,
            shutdown_grace_seconds=worker_grace + 5.0,
        )
        supervisor.start()
        logger.info("independent task watchdog started")
        while not stopped.wait(0.5):
            if parent_lost.is_set():
                break
            if not supervisor.is_alive():
                # The watchdog thread gave up (e.g. a killed worker never exited). Exit the
                # process non-zero so the API lifecycle manager restarts a fresh watchdog
                # instead of leaving the queue without any consumer.
                logger.critical("task watchdog thread stopped unexpectedly; exiting for restart")
                exit_code = 75
                break
        if parent_lost.is_set():
            logger.critical("API parent disappeared; terminating worker and watchdog")
            supervisor.abort_for_parent_loss()
    finally:
        if supervisor is not None:
            try:
                supervisor.stop()
            except Exception:
                logger.exception("task watchdog stop failed")
                exit_code = exit_code or 75
        logger.info("independent task watchdog stopped")
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
