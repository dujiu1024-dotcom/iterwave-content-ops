from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.core.scheduler import _invoke
from src.core.task_queue import GlobalTaskQueue


def test_tenant_scheduler_platform_jobs_are_enqueued_globally(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    engine = SimpleNamespace(task_queue=queue, tenant_id="tenant-b")

    asyncio.run(_invoke(engine, "_job_daily_ops"))

    task = queue.claim_next("worker-test")
    assert task is not None
    assert task.task_type == "scheduler_job"
    assert task.tenant_id == "tenant-b"
    assert task.aggregate_id == "_job_daily_ops"
    assert task.payload == {"method": "_job_daily_ops", "args": []}


def test_one_time_data_fetch_jobs_keep_draft_and_attempt_identity(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    engine = SimpleNamespace(task_queue=queue, tenant_id="tenant-c")

    asyncio.run(_invoke(engine, "_job_data_fetch", "draft-123", 2))

    task = queue.claim_next("worker-test")
    assert task is not None
    assert task.task_type == "scheduler_job"
    assert task.tenant_id == "tenant-c"
    assert task.aggregate_id == "_job_data_fetch:draft-123:2"
    assert task.payload == {
        "method": "_job_data_fetch",
        "args": ["draft-123", 2],
    }


def test_delayed_metrics_fetch_is_persisted_in_global_queue(tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from src.core.scheduler import SchedulerEngine

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    engine = SimpleNamespace(task_queue=queue, tenant_id="tenant-z")
    run_at = datetime.now(timezone.utc) + timedelta(hours=2)

    SchedulerEngine.schedule_data_fetch(engine, "draft-xyz", run_at, attempt=2)

    task = queue.find_latest(
        tenant_id="tenant-z",
        task_type="scheduler_job",
        aggregate_id="_job_data_fetch:draft-xyz:2",
    )
    assert task is not None
    assert task.status == "queued"
    assert task.payload == {
        "method": "_job_data_fetch",
        "args": ["draft-xyz", 2],
    }
    conn = queue._connect()
    try:
        row = conn.execute(
            "SELECT available_at FROM global_task_queue WHERE task_id=?",
            (task.task_id,),
        ).fetchone()
    finally:
        conn.close()
    assert datetime.fromisoformat(row["available_at"]) == run_at.astimezone(timezone.utc)


def test_user_publish_confirmation_enqueues_naive_business_time_as_utc(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from src.core.scheduler import SchedulerEngine
    from src.core.state_machine import DraftStateMachine

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE drafts (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            user_confirmed_at TEXT,
            content_id TEXT,
            platform_note_url TEXT
        );
        CREATE TABLE contents (
            id TEXT PRIMARY KEY,
            round_id INTEGER
        );
        INSERT INTO contents(id, round_id) VALUES ('content-1', NULL);
        INSERT INTO drafts(id, status, content_id)
            VALUES ('draft-1', 'draft_saved', 'content-1');
        """
    )

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    cfg = SimpleNamespace(
        business_timezone="Asia/Shanghai",
        timeouts=SimpleNamespace(metrics_initial_delay_hours=24),
    )
    monkeypatch.setattr(
        "src.core.scheduler.business_now",
        lambda _cfg: datetime(2026, 11, 1, 10, 0),
    )
    engine = SimpleNamespace(
        cfg=cfg,
        conn=conn,
        sm_draft=DraftStateMachine(conn),
        sm_round=None,
        task_queue=queue,
        tenant_id="tenant-a",
    )
    engine.schedule_data_fetch = lambda draft_id, run_at, attempt=1: (
        SchedulerEngine.schedule_data_fetch(engine, draft_id, run_at, attempt)
    )

    assert SchedulerEngine.on_user_published(engine, "draft-1", via="test") is True
    assert conn.execute(
        "SELECT status FROM drafts WHERE id='draft-1'"
    ).fetchone()["status"] == "user_published"

    task = queue.find_latest(
        tenant_id="tenant-a",
        task_type="scheduler_job",
        aggregate_id="_job_data_fetch:draft-1:1",
    )
    assert task is not None
    assert task.status == "queued"

    conn.close()
    queue_conn = queue._connect()
    try:
        stored = queue_conn.execute(
            "SELECT available_at FROM global_task_queue WHERE task_id=?",
            (task.task_id,),
        ).fetchone()["available_at"]
    finally:
        queue_conn.close()

    stored_at = datetime.fromisoformat(stored)
    assert stored_at == datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc)
