from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.adapters.base import Platform
from src.core import login_manager
from src.core.task_queue import GlobalTaskQueue
from src.core.task_worker import GlobalTaskWorker
from src.core.task_context import current_config


def test_worker_uses_task_tenant_context_not_active_login_directory(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="17",
        payload={"round_id": 17, "operation": "run"},
    )
    stop = asyncio.Event()
    observed = {}

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            observed["round_id"] = round_id
            observed["login_path"] = login_manager._platform_state_path(Platform.XHS)
            stop.set()
            return {"round_id": round_id, "final_status": "completed"}

    def build_context(tenant_id):
        assert tenant_id == "tenant-a"
        return {
            "cfg": SimpleNamespace(
                paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
                resolve_path=lambda value: value,
            ),
            "executor": FakeExecutor(),
            "scheduler": SimpleNamespace(shutdown=lambda: None),
            "conn": SimpleNamespace(close=lambda: None),
        }

    worker = GlobalTaskWorker(
        queue, build_context, poll_interval_seconds=0.01,
        lease_seconds=30, heartbeat_seconds=1,
    )
    asyncio.run(worker.run(stop))

    assert observed["round_id"] == 17
    assert observed["login_path"] == tmp_path / "tenant-a" / "data" / "login_states" / "xhs.json"
    saved = queue.get(task.task_id)
    assert saved is not None and saved.status == "succeeded"
    assert saved.result == {"round_id": 17, "final_status": "completed"}


def test_worker_cancels_execution_when_lease_renewal_fails(tmp_path, monkeypatch):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="18",
        payload={"round_id": 18, "operation": "run"},
    )
    claimed = queue.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None
    cancelled = asyncio.Event()

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(queue, "renew", lambda *args, **kwargs: False)

    def build_context(tenant_id):
        assert tenant_id == "tenant-a"
        return {
            "cfg": SimpleNamespace(
                paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
                resolve_path=lambda value: value,
            ),
            "executor": FakeExecutor(),
            "scheduler": SimpleNamespace(shutdown=lambda: None),
            "conn": SimpleNamespace(close=lambda: None),
        }

    worker = GlobalTaskWorker(
        queue, build_context, lease_seconds=30, heartbeat_seconds=0.01,
    )
    asyncio.run(worker._execute(claimed))

    assert cancelled.is_set()
    # 不得把已失去租约的任务伪装成成功或失败；恢复流程稍后会标记 interrupted。
    saved = queue.get(task.task_id)
    assert saved is not None and saved.status == "running"


def test_worker_executes_queued_scheduler_job_in_its_tenant_context(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-b",
        aggregate_id="_job_daily_ops",
        payload={"method": "_job_daily_ops", "args": []},
    )
    claimed = queue.claim_next("worker-b")
    assert claimed is not None
    observed = {}

    class FakeScheduler:
        async def _job_daily_ops(self):
            observed["login_path"] = login_manager._platform_state_path(Platform.XHS)
            observed["config"] = current_config()
            return None

        def shutdown(self):
            return None

    tenant_cfg = SimpleNamespace(
        paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-b" / "data")),
        resolve_path=lambda value: value,
    )
    context = {
        "cfg": tenant_cfg,
        "scheduler": FakeScheduler(),
        "conn": SimpleNamespace(close=lambda: None),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    result = asyncio.run(worker._run_task(claimed))

    assert result == {"completed": True}
    assert observed["login_path"] == tmp_path / "tenant-b" / "data" / "login_states" / "xhs.json"
    assert observed["config"] is tenant_cfg
    saved = queue.get(task.task_id)
    assert saved is not None and saved.status == "running"


def test_worker_survives_failure_status_write_error(tmp_path, monkeypatch):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="19",
        payload={"round_id": 19, "operation": "run"},
    )
    claimed = queue.claim_next("worker-c")
    assert claimed is not None

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            raise RuntimeError("simulated task failure")

    def build_context(tenant_id):
        return {
            "cfg": SimpleNamespace(
                paths=SimpleNamespace(data_dir=str(tmp_path / tenant_id / "data")),
                resolve_path=lambda value: value,
            ),
            "executor": FakeExecutor(),
            "scheduler": SimpleNamespace(shutdown=lambda: None),
            "conn": SimpleNamespace(close=lambda: None),
        }

    def fail_write(*args, **kwargs):
        raise OSError("simulated database outage")

    monkeypatch.setattr(queue, "fail", fail_write)
    worker = GlobalTaskWorker(
        queue, build_context, lease_seconds=30, heartbeat_seconds=1,
    )
    # 持久化失败状态的异常不能逸出 _execute 并终止全局消费者。
    asyncio.run(worker._execute(claimed))


def test_worker_redacts_sensitive_failure_details_before_queue_storage(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="20",
        payload={"round_id": 20, "operation": "run"},
    )
    claimed = queue.claim_next("worker-d")

    class SafeCodeError(RuntimeError):
        code = "provider_error"

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            raise SafeCodeError("provider rejected api_key=sk-abcdef123456")

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "executor": FakeExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": SimpleNamespace(close=lambda: None),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    worker.owner = "worker-d"
    asyncio.run(worker._execute(claimed))

    saved = queue.get(task.task_id)
    assert saved is not None and saved.status == "failed"
    assert "sk-abcdef123456" not in (saved.error_message or "")
    assert "sk-a***" in (saved.error_message or "")



def test_worker_uses_task_tenant_notifier_when_foreground_is_another_tenant(tmp_path, monkeypatch):
    """前台停留在 C 时执行 A 任务，A 的通知不得写入/发送到 C。"""
    from src.core import notify

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="21",
        payload={"round_id": 21, "operation": "run"},
    )
    claimed = queue.claim_next("worker-notifier")
    assert claimed is not None

    class RecordingNotifier:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id
            self.events = []

        def emit(self, event, message, **kwargs):
            self.events.append((event, message, kwargs))
            return True

    foreground_c = RecordingNotifier("tenant-c")
    task_a_notifier = RecordingNotifier("tenant-a")
    monkeypatch.setattr(notify, "_active", foreground_c)
    observed = {}

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            # CircuitBreakerEngine / OperationsEngine / GoalProgressUpdater
            # 的通知路径均通过 get_active() 取通知器；这里验证 worker 绑定边界。
            active = notify.get_active()
            observed["notifier_tenant"] = active.tenant_id
            active.emit("circuit_broken", "小红书触发熔断：login_expired")
            return {"round_id": round_id, "final_status": "completed"}

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "executor": FakeExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": SimpleNamespace(close=lambda: None),
        "notifier": task_a_notifier,
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    result = asyncio.run(worker._run_task(claimed))

    assert result == {"round_id": 21, "final_status": "completed"}
    assert observed["notifier_tenant"] == "tenant-a"
    assert len(task_a_notifier.events) == 1
    assert task_a_notifier.events[0][0] == "circuit_broken"
    assert foreground_c.events == []
    # ContextVar 必须在任务结束后恢复，不能污染后续任务或前台请求。
    assert notify.get_active() is foreground_c



def test_worker_with_no_task_notifier_does_not_fall_back_to_foreground(tmp_path, monkeypatch):
    """任务上下文明确没有 notifier 时，不得回退到前台账号组通知器。"""
    from src.core import notify

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="22",
        payload={"round_id": 22, "operation": "run"},
    )
    claimed = queue.claim_next("worker-no-notifier")
    assert claimed is not None

    class RecordingNotifier:
        def __init__(self):
            self.events = []

        def emit(self, event, message, **kwargs):
            self.events.append((event, message, kwargs))
            return True

    foreground_c = RecordingNotifier()
    monkeypatch.setattr(notify, "_active", foreground_c)
    observed = {}

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            observed["notifier"] = notify.get_active()
            return {"round_id": round_id, "final_status": "completed"}

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "executor": FakeExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": SimpleNamespace(close=lambda: None),
        # 故意不提供 notifier，验证明确绑定 None 时不会串到前台账号组。
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    result = asyncio.run(worker._run_task(claimed))

    assert result == {"round_id": 22, "final_status": "completed"}
    assert observed["notifier"] is None
    assert foreground_c.events == []
    # 任务结束后必须恢复前台默认通知器。
    assert notify.get_active() is foreground_c


def test_worker_shows_redacted_user_facing_precondition_error(tmp_path):
    from src.core.round_executor import RoundPreconditionError

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="23",
        payload={"round_id": 23, "operation": "run"},
    )
    claimed = queue.claim_next("worker-safe-error")

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            raise RoundPreconditionError(
                "运行前检查未通过：选题库没有可用选题。请先补充或恢复选题；api_key=sk-abcdef123456"
            )

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "executor": FakeExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": SimpleNamespace(close=lambda: None),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    worker.owner = "worker-safe-error"
    asyncio.run(worker._execute(claimed))

    saved = queue.get(task.task_id)
    assert saved is not None and saved.status == "failed"
    assert "运行前检查未通过" in (saved.error_message or "")
    assert "请先补充或恢复选题" in (saved.error_message or "")
    assert "sk-abcdef123456" not in (saved.error_message or "")
    assert "sk-a***" in (saved.error_message or "")


def test_worker_preserves_redacted_unknown_error_detail(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="24",
        payload={"round_id": 24, "operation": "run"},
    )
    claimed = queue.claim_next("worker-unknown-error")

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            raise ValueError("third-party internal failure api_key=sk-abcdef123456")

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "executor": FakeExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": SimpleNamespace(close=lambda: None),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    worker.owner = "worker-unknown-error"
    asyncio.run(worker._execute(claimed))

    saved = queue.get(task.task_id)
    assert saved is not None and saved.status == "failed"
    assert saved.error_message == "ValueError: third-party internal failure api_key=sk-a***"


def test_worker_shutdown_cancels_long_task_after_grace_and_marks_interrupted(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    first, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="first",
        payload={"round_id": 1, "operation": "run"},
    )
    second, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-b", aggregate_id="second",
        payload={"round_id": 2, "operation": "run"},
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class FakeExecutor:
        async def run(self, round_id, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    def build_context(tenant_id):
        return {
            "cfg": SimpleNamespace(
                paths=SimpleNamespace(data_dir=str(tmp_path / tenant_id / "data")),
                resolve_path=lambda value: value,
            ),
            "executor": FakeExecutor(),
            "scheduler": SimpleNamespace(shutdown=lambda: None),
            "conn": SimpleNamespace(close=lambda: None),
        }

    async def scenario():
        stop = asyncio.Event()
        worker = GlobalTaskWorker(
            queue, build_context, poll_interval_seconds=0.005,
            lease_seconds=30, heartbeat_seconds=0.01,
            shutdown_grace_seconds=0.01,
        )
        worker_task = asyncio.create_task(worker.run(stop))
        await asyncio.wait_for(started.wait(), timeout=1)
        stop.set()
        await asyncio.wait_for(worker_task, timeout=1)
        assert cancelled.is_set()

    asyncio.run(scenario())
    saved_first = queue.get(first.task_id)
    saved_second = queue.get(second.task_id)
    assert saved_first is not None and saved_first.status == "interrupted"
    assert saved_first.error_code == "worker_shutdown"
    assert saved_second is not None and saved_second.status == "queued"
    next_task = queue.claim_next("replacement-worker")
    assert next_task is not None and next_task.task_id == second.task_id


def test_worker_heartbeat_runs_in_background_thread(tmp_path):
    import threading
    import time

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="heartbeat",
        payload={"round_id": 1},
    )
    claimed = queue.claim_next("worker-heartbeat", lease_seconds=30)
    assert claimed is not None
    worker = GlobalTaskWorker(queue, lambda tenant_id: {}, heartbeat_seconds=0.01)
    worker.owner = "worker-heartbeat"
    loop = asyncio.new_event_loop()
    stopped = threading.Event()
    renewed = []
    original_renew = queue.renew

    def recording_renew(*args, **kwargs):
        renewed.append(time.monotonic())
        return original_renew(*args, **kwargs)

    queue.renew = recording_renew
    thread = threading.Thread(
        target=worker._heartbeat_loop, args=(claimed, stopped, loop, asyncio.Event()),
    )
    thread.start()
    # 在 CI 负载下线程调度可能超过固定 60ms；等待实际观察到两次续租，
    # 同时设置上限，避免测试因调度抖动产生偶发失败。
    deadline = time.monotonic() + 1.0
    while len(renewed) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stopped.set()
    thread.join(timeout=1)
    loop.close()
    assert not thread.is_alive()
    assert len(renewed) >= 2


def test_worker_fallback_marks_producing_round_failed():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE spiral_rounds (id INTEGER PRIMARY KEY, status TEXT, "
        "transition_log TEXT, producing_started_at TEXT, collecting_metrics_started_at TEXT)"
    )
    conn.execute(
        "INSERT INTO spiral_rounds(id,status,transition_log) VALUES(1,'producing','[]')"
    )
    conn.commit()

    assert GlobalTaskWorker._mark_round_failed(conn, 1, "snapshot validation failed")
    row = conn.execute("SELECT status,transition_log FROM spiral_rounds WHERE id=1").fetchone()
    assert row["status"] == "failed"
    assert '"to": "failed"' in row["transition_log"]
    conn.close()


def test_worker_fallback_does_not_override_terminal_round():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE spiral_rounds (id INTEGER PRIMARY KEY, status TEXT, "
        "transition_log TEXT, producing_started_at TEXT, collecting_metrics_started_at TEXT)"
    )
    conn.execute(
        "INSERT INTO spiral_rounds(id,status,transition_log) VALUES(1,'completed','[]')"
    )
    conn.commit()

    assert not GlobalTaskWorker._mark_round_failed(conn, 1, "late failure")
    row = conn.execute("SELECT status FROM spiral_rounds WHERE id=1").fetchone()
    assert row["status"] == "completed"
    conn.close()



def test_worker_reconciles_interrupted_round_tasks_on_startup(tmp_path, monkeypatch):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-kill", aggregate_id="41",
        payload={"round_id": 41, "operation": "run"},
    )
    claimed = queue.claim_next("worker-before-exit", lease_seconds=30)
    assert claimed is not None
    assert queue.interrupt(claimed.task_id, "worker-before-exit", error_message="模拟进程意外退出")

    observed = []
    worker = GlobalTaskWorker(queue, lambda tenant_id: {})
    def reconcile(item, reason):
        observed.append((item.task_id, item.tenant_id, reason))
        return True

    monkeypatch.setattr(worker, "_reconcile_round_failure", reconcile)
    worker._sync_terminal_rounds()

    assert observed == [(task.task_id, "tenant-kill", "模拟进程意外退出")]
    worker._sync_terminal_rounds()
    assert len(observed) == 1


def test_worker_reconciles_interrupted_rounds_beyond_first_500_queue_rows():
    from src.core.task_queue import QueuedTask

    all_tasks = [
        QueuedTask(
            queue_order=index,
            task_id=f"task-{index}",
            task_type="round_run",
            tenant_id="tenant-a",
            aggregate_id=str(index),
            payload={"round_id": index},
            status="interrupted",
            created_at="2026-11-01T00:00:00+00:00",
            available_at="2026-11-01T00:00:00+00:00",
            started_at=None,
            finished_at="2026-11-01T00:01:00+00:00",
            attempts=1,
            result=None,
            error_code="worker_restarted",
            error_message="worker restarted",
        )
        for index in range(501)
    ]
    calls = []

    class FakeQueue:
        def list_page(self, *, scope, page, page_size):
            assert scope == "failed"
            assert page_size == 500
            calls.append(page)
            start = (page - 1) * page_size
            return all_tasks[start:start + page_size], len(all_tasks)

    worker = GlobalTaskWorker(FakeQueue(), lambda _tenant_id: {})
    reconciled = []
    worker._reconcile_round_failure = lambda task, _reason: reconciled.append(task.task_id)

    worker._sync_terminal_rounds()

    assert calls == [1, 2]
    assert len(reconciled) == 501
    assert len(set(reconciled)) == 501



def test_queued_scheduler_job_binds_and_finishes_task_run_audit_context(tmp_path):
    import sqlite3
    from src.core.task_context import current_task_run_id

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a",
        aggregate_id="_job_daily_ops",
        payload={"method": "_job_daily_ops", "args": []},
    )
    claimed = queue.claim_next("worker-a")
    assert claimed is not None

    audit_conn = sqlite3.connect(":memory:")
    audit_conn.row_factory = sqlite3.Row
    audit_conn.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT, "
        "biz_id TEXT, status TEXT, attempt INTEGER, started_at TEXT, finished_at TEXT, "
        "error_msg TEXT, created_at TEXT)"
    )
    audit_conn.commit()
    observed = {}

    class FakeScheduler:
        async def _job_daily_ops(self):
            observed["task_run_id"] = current_task_run_id()
            return None

        def shutdown(self):
            return None

    class ConnectionProxy:
        def __enter__(self):
            audit_conn.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return audit_conn.__exit__(exc_type, exc_value, traceback)

        def execute(self, *args):
            return audit_conn.execute(*args)

        def commit(self):
            audit_conn.commit()

        def close(self):
            # Keep the in-memory connection available for assertions after _run_task.
            return None

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "scheduler": FakeScheduler(),
        "conn": ConnectionProxy(),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    result = asyncio.run(worker._run_task(claimed))

    assert result == {"completed": True}
    assert observed["task_run_id"] is not None
    row = audit_conn.execute(
        "SELECT task_type, biz_id, status, finished_at FROM task_runs WHERE id=?",
        (observed["task_run_id"],),
    ).fetchone()
    assert row["task_type"] == "daily_ops"
    assert row["biz_id"].startswith("scheduler:_job_daily_ops:")
    assert row["status"] == "success"
    assert row["finished_at"]
    assert current_task_run_id() is None
    audit_conn.close()


def test_worker_marks_structured_failed_round_report_as_queue_failure(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="round-501",
        payload={"round_id": 501, "operation": "run"},
    )
    claimed = queue.claim_next("worker-failed-report")
    assert claimed is not None

    class EmptyCursor:
        def fetchone(self):
            return None

    class FakeConnection:
        def execute(self, *args, **kwargs):
            return EmptyCursor()

        def commit(self):
            return None

        def close(self):
            return None

    class FailedExecutor:
        async def run(self, round_id, **kwargs):
            return {
                "round_id": round_id,
                "final_status": "failed",
                "errors": ["模型服务未配置"],
            }

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "executor": FailedExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": FakeConnection(),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    worker.owner = "worker-failed-report"
    worker._reconcile_round_failure = lambda task, reason: None

    asyncio.run(worker._execute(claimed))

    saved = queue.get(task.task_id)
    assert saved is not None
    assert saved.status == "failed"
    assert saved.error_code == "task_execution_failed"
    assert "模型服务未配置" in (saved.error_message or "")


def test_worker_marks_scheduler_task_run_failed_for_structured_failure_report(tmp_path):
    import sqlite3
    from src.core.task_context import current_task_run_id

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a",
        aggregate_id="_job_daily_ops",
        payload={"method": "_job_daily_ops", "args": []},
    )
    claimed = queue.claim_next("worker-scheduler-failed")
    assert claimed is not None

    audit_conn = sqlite3.connect(":memory:")
    audit_conn.row_factory = sqlite3.Row
    audit_conn.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT, "
        "biz_id TEXT, status TEXT, attempt INTEGER, started_at TEXT, finished_at TEXT, "
        "error_msg TEXT, created_at TEXT)"
    )
    audit_conn.commit()
    observed = {}

    class ConnectionProxy:
        def __enter__(self):
            audit_conn.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return audit_conn.__exit__(exc_type, exc_value, traceback)

        def execute(self, *args):
            return audit_conn.execute(*args)

        def commit(self):
            audit_conn.commit()

        def close(self):
            return None

    class FailedScheduler:
        async def _job_daily_ops(self):
            observed["task_run_id"] = current_task_run_id()
            return {"final_status": "failed", "errors": ["运营配置读取失败"]}

        def shutdown(self):
            return None

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "scheduler": FailedScheduler(),
        "conn": ConnectionProxy(),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    result = asyncio.run(worker._run_task(claimed))

    assert result["final_status"] == "failed"
    row = audit_conn.execute(
        "SELECT task_type, status, error_msg FROM task_runs WHERE id=?",
        (observed["task_run_id"],),
    ).fetchone()
    assert row["task_type"] == "daily_ops"
    assert row["status"] == "failed"
    assert "运营配置读取失败" in row["error_msg"]
    audit_conn.close()


def test_worker_dispatches_hot_topic_refresh_as_a_tracked_scheduler_job(tmp_path):
    import sqlite3

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a",
        aggregate_id="_job_hot_topics_refresh",
        payload={"method": "_job_hot_topics_refresh", "args": []},
    )
    claimed = queue.claim_next("worker-hot-topics")
    assert claimed is not None and claimed.task_id == task.task_id

    audit_conn = sqlite3.connect(":memory:")
    audit_conn.row_factory = sqlite3.Row
    audit_conn.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT, "
        "biz_id TEXT, status TEXT, attempt INTEGER, started_at TEXT, finished_at TEXT, "
        "error_msg TEXT, created_at TEXT)"
    )
    audit_conn.commit()

    class ConnectionProxy:
        def __enter__(self):
            audit_conn.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return audit_conn.__exit__(exc_type, exc_value, traceback)

        def execute(self, *args):
            return audit_conn.execute(*args)

        def commit(self):
            audit_conn.commit()

        def close(self):
            return None

    class FakeScheduler:
        async def _job_hot_topics_refresh(self):
            return {"final_status": "failed", "errors": ["有 1 个热点来源采集失败"]}

        def shutdown(self):
            return None

    context = {
        "cfg": SimpleNamespace(
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "scheduler": FakeScheduler(),
        "conn": ConnectionProxy(),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    result = asyncio.run(worker._run_task(claimed))

    assert result["final_status"] == "failed"
    row = audit_conn.execute(
        "SELECT task_type, status, error_msg FROM task_runs"
    ).fetchone()
    assert row["task_type"] == "hot_topics_refresh"
    assert row["status"] == "failed"
    assert "热点来源采集失败" in row["error_msg"]
    audit_conn.close()


def test_scheduler_exception_audit_uses_safe_message(tmp_path):
    import sqlite3
    import pytest

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a",
        aggregate_id="_job_daily_ops",
        payload={"method": "_job_daily_ops", "args": []},
    )
    claimed = queue.claim_next("worker-scheduler-exception")
    assert claimed is not None

    audit_conn = sqlite3.connect(":memory:")
    audit_conn.row_factory = sqlite3.Row
    audit_conn.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT, "
        "biz_id TEXT, status TEXT, attempt INTEGER, started_at TEXT, finished_at TEXT, "
        "error_msg TEXT, created_at TEXT)"
    )
    audit_conn.commit()

    class ConnectionProxy:
        def __enter__(self):
            audit_conn.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return audit_conn.__exit__(exc_type, exc_value, traceback)

        def execute(self, *args):
            return audit_conn.execute(*args)
        def commit(self):
            audit_conn.commit()
        def close(self):
            return None

    class BrokenScheduler:
        async def _job_daily_ops(self):
            raise RuntimeError("https://internal.example/private?token=secret")
        def shutdown(self):
            return None

    context = {
        "cfg": SimpleNamespace(
            business_timezone="Asia/Shanghai",
            paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
            resolve_path=lambda value: value,
        ),
        "scheduler": BrokenScheduler(),
        "conn": ConnectionProxy(),
    }
    worker = GlobalTaskWorker(queue, lambda tenant_id: context)
    with pytest.raises(RuntimeError, match="internal.example"):
        asyncio.run(worker._run_task(claimed))

    row = audit_conn.execute("SELECT error_msg FROM task_runs").fetchone()
    assert row["error_msg"] == "定时任务执行异常，请检查服务端运行日志"
    assert "internal.example" not in row["error_msg"]
    assert "secret" not in row["error_msg"]
    audit_conn.close()


def test_interrupted_round_reconciliation_retries_after_transient_context_failure(tmp_path, monkeypatch):
    """一次性上下文/数据库故障不能让进程内缓存永久跳过 interrupted 对账。"""
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="25",
        payload={"round_id": 25, "operation": "run"},
    )
    claimed = queue.claim_next("worker-reconcile")
    assert claimed is not None
    assert queue.interrupt(task.task_id, "worker-reconcile")

    calls = {"build": 0, "round": 0, "plan": 0}

    class FakeConnection:
        def close(self):
            pass

    def build_context(_tenant_id):
        calls["build"] += 1
        if calls["build"] == 1:
            raise OSError("temporary tenant database unavailable")
        return {
            "conn": FakeConnection(),
            "scheduler": SimpleNamespace(shutdown=lambda: None),
        }

    monkeypatch.setattr(
        GlobalTaskWorker, "_mark_round_failed",
        staticmethod(lambda *_args, **_kwargs: calls.__setitem__("round", calls["round"] + 1) or True),
    )
    monkeypatch.setattr(
        GlobalTaskWorker, "_mark_content_plan_failed",
        staticmethod(lambda *_args, **_kwargs: calls.__setitem__("plan", calls["plan"] + 1)),
    )
    worker = GlobalTaskWorker(queue, build_context)

    worker._sync_terminal_rounds()
    assert task.task_id not in worker._reconciled_interrupted_task_ids
    assert calls == {"build": 1, "round": 0, "plan": 0}

    worker._sync_terminal_rounds()
    assert task.task_id in worker._reconciled_interrupted_task_ids
    assert calls == {"build": 2, "round": 1, "plan": 1}

    # 对账成功后不重复执行数据库修复。
    worker._sync_terminal_rounds()
    assert calls == {"build": 2, "round": 1, "plan": 1}



def test_worker_content_plan_completion_is_atomic_on_late_failure():
    import sqlite3
    from src.core.task_worker import GlobalTaskWorker

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE content_plan_rounds (round_id INTEGER, plan_id TEXT);
        CREATE TABLE content_plans (id TEXT PRIMARY KEY, status TEXT, version INTEGER, updated_at TEXT);
        CREATE TABLE contents (id TEXT PRIMARY KEY, round_id INTEGER);
        CREATE TABLE drafts (id TEXT PRIMARY KEY, content_id TEXT);
        CREATE TABLE content_plan_drafts (
            plan_id TEXT, draft_id TEXT, round_id INTEGER, created_at TEXT,
            PRIMARY KEY(plan_id, draft_id)
        );
        INSERT INTO content_plan_rounds VALUES (7, 'plan-7');
        INSERT INTO content_plans VALUES ('plan-7', 'generating', 1, NULL);
        INSERT INTO contents VALUES ('content-7', 7);
        INSERT INTO drafts VALUES ('draft-7', 'content-7');
        CREATE TRIGGER reject_plan_completion
        BEFORE UPDATE ON content_plans
        BEGIN SELECT RAISE(ABORT, 'simulated plan status write failure'); END;
    """)

    try:
        GlobalTaskWorker._complete_content_plan(
            conn, 7, {"final_status": "completed"},
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected simulated plan status failure")

    assert conn.execute(
        "SELECT COUNT(*) FROM content_plan_drafts WHERE plan_id='plan-7'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT status, version FROM content_plans WHERE id='plan-7'"
    ).fetchone()[:] == ("generating", 1)
    conn.close()


def test_worker_content_plan_completion_commits_links_and_status_together():
    import sqlite3
    from src.core.task_worker import GlobalTaskWorker

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE content_plan_rounds (round_id INTEGER, plan_id TEXT);
        CREATE TABLE content_plans (id TEXT PRIMARY KEY, status TEXT, version INTEGER, updated_at TEXT);
        CREATE TABLE contents (id TEXT PRIMARY KEY, round_id INTEGER);
        CREATE TABLE drafts (id TEXT PRIMARY KEY, content_id TEXT);
        CREATE TABLE content_plan_drafts (
            plan_id TEXT, draft_id TEXT, round_id INTEGER, created_at TEXT,
            PRIMARY KEY(plan_id, draft_id)
        );
        INSERT INTO content_plan_rounds VALUES (8, 'plan-8');
        INSERT INTO content_plans VALUES ('plan-8', 'generating', 3, NULL);
        INSERT INTO contents VALUES ('content-8', 8);
        INSERT INTO drafts VALUES ('draft-8', 'content-8');
    """)

    GlobalTaskWorker._complete_content_plan(
        conn, 8, {"final_status": "completed"},
    )

    assert conn.execute(
        "SELECT plan_id, draft_id, round_id FROM content_plan_drafts"
    ).fetchone()[:] == ("plan-8", "draft-8", 8)
    assert conn.execute(
        "SELECT status, version FROM content_plans WHERE id='plan-8'"
    ).fetchone()[:] == ("generated", 4)
    conn.close()



def test_scheduler_task_run_audit_writes_use_connection_transaction_context():
    import sqlite3
    from src.core.task_worker import GlobalTaskWorker
    from src.core.timezone import DEFAULT_TIMEZONE
    from types import SimpleNamespace

    class TrackingConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.transaction_entries = 0

        def __enter__(self):
            self.transaction_entries += 1
            return super().__enter__()

    conn = sqlite3.connect(":memory:", factory=TrackingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT, biz_id TEXT, status TEXT, attempt INTEGER,
            started_at TEXT, created_at TEXT, finished_at TEXT, error_msg TEXT
        )
    """)
    cfg = SimpleNamespace(business_timezone=DEFAULT_TIMEZONE)

    task_run_id = GlobalTaskWorker._start_scheduler_task_run(
        conn, cfg, "daily_ops", "_job_daily_ops", [],
    )
    assert task_run_id == 1
    assert conn.execute(
        "SELECT status FROM task_runs WHERE id=?", (task_run_id,),
    ).fetchone()["status"] == "running"

    GlobalTaskWorker._finish_scheduler_task_run(
        conn, cfg, task_run_id, "success", None,
    )
    saved = conn.execute(
        "SELECT status, finished_at FROM task_runs WHERE id=?", (task_run_id,),
    ).fetchone()
    assert saved["status"] == "success"
    assert saved["finished_at"]
    assert conn.transaction_entries == 2
    conn.close()
