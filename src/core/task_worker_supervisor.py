"""Independent watchdog and OS-process supervisor for the global task worker."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def process_start_token(pid: int) -> str | None:
    """Return Linux /proc starttime so a reused PID is not mistaken for its parent."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        tail = raw[raw.rfind(")") + 2:].split()
        return tail[19]
    except (OSError, ValueError, IndexError):
        return None


def parent_process_alive(pid: int, expected_start_token: str | None = None) -> bool:
    if pid <= 1 or os.getppid() != pid:
        return False
    if expected_start_token:
        actual = process_start_token(pid)
        # If the platform supplied a start token, inability to verify it must not
        # silently downgrade the parent identity check to PID-only matching.
        if actual is None or actual != expected_start_token:
            return False
    return True


def _positive_seconds(value: str | None, default: float, *, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        return default
    return min(parsed, maximum)


def _age_seconds(started_at: str | None, *, now: datetime | None = None) -> float | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current - started).total_seconds())
    except (TypeError, ValueError):
        return None


class TaskWorkerSupervisor:
    """Run the queue consumer outside the API process and enforce a hard wall-clock limit.

    This object runs inside the standalone watchdog process, separate from both API and
    task consumer. On timeout it kills the entire worker process group (including browser
    children), then atomically marks running work interrupted. It never retries uncertain
    platform side effects.
    """

    def __init__(
        self,
        queue: Any,
        *,
        project_root: str | Path,
        hard_timeout_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
        shutdown_grace_seconds: float = 20.0,
        popen_factory=subprocess.Popen,
    ) -> None:
        self.queue = queue
        self.project_root = Path(project_root).resolve()
        env_timeout = os.getenv("ITERWAVE_TASK_HARD_TIMEOUT_SECONDS")
        self.hard_timeout_seconds = min(
            max(30.0, float(hard_timeout_seconds)) if hard_timeout_seconds is not None
            else _positive_seconds(env_timeout, 1800.0, maximum=86400.0),
            86400.0,
        )
        self.poll_interval_seconds = max(0.1, min(float(poll_interval_seconds), 10.0))
        self.shutdown_grace_seconds = max(0.0, min(float(shutdown_grace_seconds), 120.0))
        self._popen = popen_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watchdog_loop, name="iterwave-task-watchdog", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout or self.shutdown_grace_seconds + 20.0)
            if thread.is_alive():
                raise RuntimeError("task worker watchdog did not stop within its shutdown deadline")
        self._thread = None

    def _spawn(self) -> subprocess.Popen:
        command = [sys.executable, "-m", "src.core.task_worker_service"]
        worker_env = os.environ.copy()
        worker_env["ITERWAVE_TASK_SUPERVISOR_PID"] = str(os.getpid())
        token = process_start_token(os.getpid())
        if token:
            worker_env["ITERWAVE_TASK_SUPERVISOR_START_TOKEN"] = token
        kwargs: dict[str, Any] = {
            "cwd": str(self.project_root),
            "env": worker_env,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        process = self._popen(command, **kwargs)
        logger.info("dedicated task worker started pid=%s", process.pid)
        return process

    def _running_tasks(self) -> list[Any]:
        # Do not page through the mutable active queue: as tasks finish between pages,
        # offset pagination can skip a still-running task. Read all running rows in one
        # SQLite statement so every watchdog cycle evaluates the complete running set.
        return self.queue.list_running_tasks()

    def _overdue_tasks(self, *, now: datetime | None = None) -> list[Any]:
        overdue = []
        for task in self._running_tasks():
            if getattr(task, "status", "running") != "running":
                continue
            age = _age_seconds(task.started_at, now=now)
            # A running task without a valid start timestamp has no enforceable deadline.
            # Fail closed by terminating the worker rather than allowing an unbounded run.
            if age is None or age >= self.hard_timeout_seconds:
                overdue.append(task)
        return overdue

    def _terminate_process(self, process: subprocess.Popen, *, force: bool) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                if force:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                else:
                    process.terminate()
            else:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("failed to signal task worker process group pid=%s", process.pid)

    def _stop_process(self, process: subprocess.Popen) -> None:
        self._terminate_process(process, force=False)
        try:
            process.wait(timeout=self.shutdown_grace_seconds)
        except subprocess.TimeoutExpired:
            logger.error("task worker exceeded shutdown grace; killing process group pid=%s", process.pid)
            self._terminate_process(process, force=True)
            process.wait(timeout=10.0)

    def _interrupt_after_exit(self, reason: str) -> None:
        try:
            report = self.queue.interrupt_running_after_worker_exit(reason=reason)
            if report.get("interrupted"):
                logger.error("worker process loss reconciled: %s", report)
        except Exception:
            logger.exception("failed to reconcile tasks after worker process exit")

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def abort_for_parent_loss(self) -> None:
        """Hard-stop the worker if the API parent disappears unexpectedly."""
        self._stop.set()
        process = self._process
        if process is not None:
            self._terminate_process(process, force=True)
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired as exc:
                logger.critical("worker process exit was not confirmed pid=%s", process.pid)
                raise RuntimeError("worker process exit was not confirmed") from exc
            if process.poll() is None:
                raise RuntimeError("worker process exit was not confirmed")
            self._process = None
            self._interrupt_after_exit(
                "API 服务进程意外退出；Worker 已确认终止，任务结果需人工核实"
            )
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15.0)

    def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            process = self._process
            if process is None:
                try:
                    self._process = self._spawn()
                except Exception:
                    logger.exception("failed to start dedicated task worker")
                    self._stop.wait(min(5.0, self.poll_interval_seconds * 5))
                    continue
                process = self._process

            exit_code = process.poll()
            if exit_code is not None:
                logger.error("dedicated task worker exited unexpectedly exit_code=%s", exit_code)
                self._process = None
                self._interrupt_after_exit("后台 Worker 进程异常退出；请先核实平台侧结果，再人工恢复任务")
                self._stop.wait(min(2.0, self.poll_interval_seconds * 2))
                continue

            try:
                overdue = self._overdue_tasks()
            except Exception:
                logger.exception("independent task watchdog could not inspect queue")
                self._stop.wait(self.poll_interval_seconds)
                continue

            if overdue:
                task = max(overdue, key=lambda item: _age_seconds(item.started_at) or 0.0)
                logger.error(
                    "hard task timeout; killing worker process group task_id=%s tenant_id=%s age=%.1fs",
                    task.task_id, task.tenant_id, _age_seconds(task.started_at) or 0.0,
                )
                self._terminate_process(process, force=True)
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    logger.critical("worker process group did not exit after SIGKILL pid=%s", process.pid)
                    self._stop.set()
                    continue
                self._process = None
                self._interrupt_after_exit(
                    "任务超过硬超时，Worker 进程及其浏览器子进程已终止；平台侧结果需人工核实后再恢复"
                )
                continue

            self._stop.wait(self.poll_interval_seconds)

        process = self._process
        self._process = None
        if process is not None:
            self._stop_process(process)
            if process.poll() is not None:
                self._interrupt_after_exit(
                    "服务关闭时 Worker 进程已停止；未完成任务已中断，请核实平台侧结果"
                )
