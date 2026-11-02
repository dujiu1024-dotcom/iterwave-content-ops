"""Lifecycle manager that keeps the independent watchdog alive while the API is alive."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from src.core.task_worker_supervisor import process_start_token

logger = logging.getLogger(__name__)


class TaskWorkerSupervisorProcess:
    def __init__(
        self, *, project_root: str | Path, shutdown_timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 1.0, popen_factory=subprocess.Popen,
    ):
        self.project_root = Path(project_root).resolve()
        self.shutdown_timeout_seconds = max(10.0, min(float(shutdown_timeout_seconds), 120.0))
        self.poll_interval_seconds = max(0.2, min(float(poll_interval_seconds), 5.0))
        self.process: subprocess.Popen | None = None
        self._popen = popen_factory
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        # Serialize spawn/restart/stop so the monitor cannot create a replacement
        # watchdog after lifespan shutdown has begun.
        self._lifecycle_lock = threading.RLock()

    def _spawn(self) -> subprocess.Popen:
        child_env = os.environ.copy()
        child_env["ITERWAVE_API_PARENT_PID"] = str(os.getpid())
        token = process_start_token(os.getpid())
        if token:
            child_env["ITERWAVE_API_PARENT_START_TOKEN"] = token
        kwargs = {
            "cwd": str(self.project_root),
            "env": child_env,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        process = self._popen(
            [sys.executable, "-m", "src.core.task_worker_supervisor_service"],
            **kwargs,
        )
        logger.info("independent task watchdog process started pid=%s", process.pid)
        return process

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stop.is_set():
                self._stop.clear()
            if self.process is not None and self.process.poll() is None:
                return
            self.process = self._spawn()
            self._monitor = threading.Thread(
                target=self._monitor_loop, name="iterwave-watchdog-process-monitor", daemon=True,
            )
            self._monitor.start()

    def _monitor_loop(self) -> None:
        while not self._stop.wait(self.poll_interval_seconds):
            try:
                with self._lifecycle_lock:
                    if self._stop.is_set():
                        return
                    process = self.process
                    if process is not None and process.poll() is None:
                        continue
                    logger.error("independent watchdog exited; restarting supervisor process")
                    self.process = self._spawn()
            except Exception:
                logger.exception("failed to restart independent task watchdog")
                self._stop.wait(min(5.0, self.poll_interval_seconds * 5))

    def stop(self) -> None:
        self._stop.set()
        with self._lifecycle_lock:
            process = self.process
            self.process = None
        if process is not None and process.poll() is None:
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=self.shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                logger.error("watchdog process exceeded shutdown deadline; force terminating pid=%s", process.pid)
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10.0)
            except ProcessLookupError:
                pass
        monitor = self._monitor
        if monitor is not None:
            monitor.join(timeout=5.0)
            if monitor.is_alive():
                raise RuntimeError("watchdog process monitor did not stop")
        self._monitor = None
