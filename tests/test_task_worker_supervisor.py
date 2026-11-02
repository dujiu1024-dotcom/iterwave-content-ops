from __future__ import annotations

import os
import signal
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.core.task_worker_supervisor import (
    TaskWorkerSupervisor,
    _age_seconds,
    _positive_seconds,
)


def test_age_seconds_handles_utc_and_naive_timestamps():
    now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    assert _age_seconds("2026-11-02T09:59:00+00:00", now=now) == 60
    assert _age_seconds("2026-11-02T09:59:00", now=now) == 60
    assert _age_seconds("not-a-date", now=now) is None


def test_hard_timeout_configuration_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("ITERWAVE_TASK_HARD_TIMEOUT_SECONDS", "999999")
    supervisor = TaskWorkerSupervisor(
        SimpleNamespace(), project_root=tmp_path, poll_interval_seconds=0.1,
    )
    assert supervisor.hard_timeout_seconds == 86400

    monkeypatch.setenv("ITERWAVE_TASK_HARD_TIMEOUT_SECONDS", "invalid")
    fallback = TaskWorkerSupervisor(SimpleNamespace(), project_root=tmp_path)
    assert fallback.hard_timeout_seconds == 1800
    assert _positive_seconds("0", 10, maximum=20) == 10


def test_watchdog_selects_only_running_tasks_past_hard_deadline(tmp_path):
    now = datetime.now(timezone.utc)
    tasks = [
        SimpleNamespace(status="running", started_at=(now - timedelta(seconds=120)).isoformat()),
        SimpleNamespace(status="running", started_at=(now - timedelta(seconds=10)).isoformat()),
        SimpleNamespace(status="queued", started_at=(now - timedelta(seconds=300)).isoformat()),
    ]

    class Queue:
        def list_running_tasks(self):
            return tasks

    supervisor = TaskWorkerSupervisor(
        Queue(), project_root=tmp_path, hard_timeout_seconds=60,
    )
    overdue = supervisor._overdue_tasks(now=now)
    assert overdue == [tasks[0]]


def test_process_group_termination_uses_sigkill_on_posix(monkeypatch, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX process groups are not available on Windows")
    calls = []

    class Process:
        pid = 456
        def poll(self):
            return None

    monkeypatch.setattr("src.core.task_worker_supervisor.os.killpg",
                        lambda pid, sig: calls.append((pid, sig)))
    supervisor = TaskWorkerSupervisor(SimpleNamespace(), project_root=tmp_path)
    supervisor._terminate_process(Process(), force=True)

    assert calls == [(456, signal.SIGKILL)]


def test_parent_identity_guard_detects_supervisor_exit_and_pid_reuse(monkeypatch):
    from src.core import task_worker_supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module.os, "getppid", lambda: 123)
    monkeypatch.setattr(supervisor_module, "process_start_token", lambda _pid: "new-process")
    assert supervisor_module.parent_process_alive(123, "new-process")
    assert not supervisor_module.parent_process_alive(123, "old-process")
    assert not supervisor_module.parent_process_alive(456)


def test_parent_loss_aborts_worker_process_and_interrupts_queue(tmp_path, monkeypatch):
    events = []

    class Queue:
        def interrupt_running_after_worker_exit(self, *, reason):
            events.append(("interrupt", reason))
            return {"interrupted": 1, "task_ids": ["task-1"]}

    class Process:
        pid = 789
        exit_code = None
        def poll(self): return self.exit_code
        def wait(self, timeout=None):
            events.append(("wait", timeout))
            self.exit_code = -9
            return self.exit_code

    supervisor = TaskWorkerSupervisor(Queue(), project_root=tmp_path)
    supervisor._process = Process()
    monkeypatch.setattr(
        supervisor, "_terminate_process",
        lambda process, force: events.append(("terminate", process.pid, force)),
    )

    supervisor.abort_for_parent_loss()

    assert supervisor._stop.is_set()
    assert supervisor._process is None
    assert events[0] == ("terminate", 789, True)
    assert events[-1][0] == "interrupt"


def test_api_process_manager_starts_watchdog_with_parent_identity(tmp_path):
    from src.core.task_worker_process_manager import TaskWorkerSupervisorProcess

    captured = {}

    class Process:
        pid = 321
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    manager = TaskWorkerSupervisorProcess(
        project_root=tmp_path, popen_factory=fake_popen,
    )
    process = manager._spawn()

    assert process.pid == 321
    assert captured["command"][-1] == "src.core.task_worker_supervisor_service"
    env = captured["kwargs"]["env"]
    assert int(env["ITERWAVE_API_PARENT_PID"]) > 0
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())


def test_watchdog_kills_worker_before_marking_tasks_interrupted(tmp_path, monkeypatch):
    events = []
    task = SimpleNamespace(task_id="task-1", tenant_id="tenant-a", started_at="2026-11-02T00:00:00+00:00")

    class Queue:
        def interrupt_running_after_worker_exit(self, *, reason):
            events.append(("interrupt", reason))
            supervisor._stop.set()
            return {"interrupted": 1, "task_ids": ["task-1"]}

    class Process:
        pid = 456
        def poll(self):
            return None
        def wait(self, timeout=None):
            events.append(("wait", timeout))
            return -9

    queue = Queue()
    supervisor = TaskWorkerSupervisor(
        queue, project_root=tmp_path, hard_timeout_seconds=30,
    )
    process = Process()
    supervisor._process = process
    monkeypatch.setattr(supervisor, "_overdue_tasks", lambda: [task])
    monkeypatch.setattr(
        supervisor, "_terminate_process",
        lambda proc, force: events.append(("kill", proc.pid, force)),
    )

    supervisor._watchdog_loop()

    assert events[0] == ("kill", 456, True)
    assert events[1] == ("wait", 10.0)
    assert events[2][0] == "interrupt"
    assert supervisor._process is None


def test_graceful_shutdown_deadlines_allow_worker_to_finish_before_watchdog_exit(tmp_path):
    from src.core.task_worker_process_manager import TaskWorkerSupervisorProcess
    from src.core.task_worker import GlobalTaskWorker

    worker = GlobalTaskWorker(SimpleNamespace(), lambda _tenant: {}, shutdown_grace_seconds=200)
    watchdog = TaskWorkerSupervisor(
        SimpleNamespace(), project_root=tmp_path, shutdown_grace_seconds=worker.shutdown_grace_seconds + 5,
    )
    api_manager = TaskWorkerSupervisorProcess(project_root=tmp_path)

    assert worker.shutdown_grace_seconds == 90
    assert watchdog.shutdown_grace_seconds == 95
    assert api_manager.shutdown_timeout_seconds == 120



def test_watchdog_fails_closed_when_running_task_has_no_valid_start_time(tmp_path):
    tasks = [
        SimpleNamespace(status="running", started_at=None),
        SimpleNamespace(status="running", started_at="invalid-timestamp"),
    ]

    class Queue:
        def list_running_tasks(self):
            return tasks

    supervisor = TaskWorkerSupervisor(
        Queue(), project_root=tmp_path, hard_timeout_seconds=60,
    )
    assert supervisor._overdue_tasks() == tasks



def test_watchdog_checks_all_running_tasks_without_500_row_truncation(tmp_path):
    now = datetime.now(timezone.utc)
    old_task = SimpleNamespace(
        task_id="old-running-task", status="running",
        started_at=(now - timedelta(seconds=120)).isoformat(),
    )
    tasks = [
        SimpleNamespace(status="running", started_at=(now - timedelta(seconds=5)).isoformat())
        for _ in range(501)
    ]
    tasks.append(old_task)

    class Queue:
        def list_running_tasks(self):
            return tasks

    supervisor = TaskWorkerSupervisor(
        Queue(), project_root=tmp_path, hard_timeout_seconds=60,
    )
    assert supervisor._overdue_tasks(now=now) == [old_task]



def test_parent_identity_guard_fails_closed_when_start_token_is_unreadable(monkeypatch):
    from src.core import task_worker_supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module.os, "getppid", lambda: 123)
    monkeypatch.setattr(supervisor_module, "process_start_token", lambda _pid: None)
    assert not supervisor_module.parent_process_alive(123, "expected-token")


def test_parent_loss_does_not_reconcile_when_worker_exit_is_unconfirmed(tmp_path, monkeypatch):
    events = []

    class Queue:
        def interrupt_running_after_worker_exit(self, *, reason):
            events.append(("interrupt", reason))
            return {"interrupted": 1, "task_ids": ["task-1"]}

    class Process:
        pid = 790
        def poll(self): return None
        def wait(self, timeout=None):
            events.append(("wait", timeout))
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    supervisor = TaskWorkerSupervisor(Queue(), project_root=tmp_path)
    process = Process()
    supervisor._process = process
    monkeypatch.setattr(
        supervisor, "_terminate_process",
        lambda proc, force: events.append(("terminate", proc.pid, force)),
    )

    with pytest.raises(RuntimeError, match="exit was not confirmed"):
        supervisor.abort_for_parent_loss()

    assert supervisor._process is process
    assert not any(event[0] == "interrupt" for event in events)


def test_worker_count_defaults_to_single_global_fifo_consumer():
    from src.core.task_worker_service import resolve_worker_count

    assert resolve_worker_count(None) == 1
    assert resolve_worker_count("") == 1
    assert resolve_worker_count("abc") == 1
    assert resolve_worker_count("0") == 1
    assert resolve_worker_count("3") == 3
    assert resolve_worker_count("99") == 8


def test_worker_and_watchdog_shutdown_grace_bounds_match():
    from src.core.task_worker_service import resolve_shutdown_grace

    assert resolve_shutdown_grace(None) == 30.0
    assert resolve_shutdown_grace("200") == 90.0
    assert resolve_shutdown_grace("-1") == 30.0


def test_supervisor_reports_dead_watchdog_thread(tmp_path):
    supervisor = TaskWorkerSupervisor(SimpleNamespace(), project_root=tmp_path)
    assert not supervisor.is_alive()
