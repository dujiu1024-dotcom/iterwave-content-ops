from __future__ import annotations

import threading
from pathlib import Path

from src.core.task_queue import GlobalTaskQueue


def make_queue(tmp_path: Path) -> GlobalTaskQueue:
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    return queue


def test_fifo_is_global_across_tenants(tmp_path):
    queue = make_queue(tmp_path)
    a, _ = queue.enqueue(task_type="round_run", tenant_id="a", aggregate_id="1", payload={"round_id": 1})
    b, _ = queue.enqueue(task_type="round_run", tenant_id="b", aggregate_id="2", payload={"round_id": 2})
    c, _ = queue.enqueue(task_type="round_run", tenant_id="c", aggregate_id="3", payload={"round_id": 3})
    d, _ = queue.enqueue(task_type="round_run", tenant_id="d", aggregate_id="4", payload={"round_id": 4})

    first = queue.claim_next("worker-1")
    assert first and first.task_id == a.task_id and first.tenant_id == "a"
    assert queue.finish(first.task_id, "worker-1", result={"ok": True})
    second = queue.claim_next("worker-1")
    assert second and second.task_id == b.task_id and second.tenant_id == "b"
    assert queue.finish(second.task_id, "worker-1", result={"ok": True})
    third = queue.claim_next("worker-1")
    assert third and third.task_id == c.task_id and third.tenant_id == "c"
    assert queue.finish(third.task_id, "worker-1", result={"ok": True})
    fourth = queue.claim_next("worker-1")
    assert fourth and fourth.task_id == d.task_id and fourth.tenant_id == "d"


def test_active_aggregate_is_idempotent(tmp_path):
    queue = make_queue(tmp_path)
    first, created = queue.enqueue(
        task_type="round_run", tenant_id="a", aggregate_id="42", payload={"round_id": 42},
    )
    again, created_again = queue.enqueue(
        task_type="round_run", tenant_id="a", aggregate_id="42", payload={"round_id": 42},
    )
    assert created is True
    assert created_again is False
    assert first.task_id == again.task_id



def test_failure_releases_queue_for_next_task(tmp_path):
    queue = make_queue(tmp_path)
    first, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={"n": 1})
    second, _ = queue.enqueue(task_type="round_run", tenant_id="b", payload={"n": 2})
    claimed = queue.claim_next("worker-1")
    assert claimed and claimed.task_id == first.task_id
    assert queue.fail(first.task_id, "worker-1", error_code="failed", error_message="safe")
    next_task = queue.claim_next("worker-1")
    assert next_task and next_task.task_id == second.task_id


def test_only_queued_tasks_can_be_cancelled(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={})
    assert queue.cancel(task.task_id)
    assert not queue.cancel(task.task_id)
    assert queue.get(task.task_id).status == "cancelled"


def test_running_task_is_not_replayed_after_worker_loss(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={})
    claimed = queue.claim_next("worker-1", lease_seconds=15)
    assert claimed and claimed.task_id == task.task_id
    # Force lease expiry deterministically to simulate a process crash.
    conn = queue._connect()
    try:
        conn.execute(
            "UPDATE global_task_queue SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (task.task_id,),
        )
        conn.execute(
            "UPDATE global_task_worker_lease SET expires_at='2000-01-01T00:00:00+00:00' WHERE singleton_id=1"
        )
    finally:
        conn.close()
    assert queue.recover_expired() == {"interrupted": 1}
    assert queue.get(task.task_id).status == "interrupted"
    assert queue.claim_next("worker-2") is None


def test_concurrent_duplicate_enqueue_creates_one_active_task(tmp_path):
    queue = make_queue(tmp_path)
    results = []

    def submit():
        results.append(queue.enqueue(
            task_type="round_run", tenant_id="a", aggregate_id="99", payload={"round_id": 99},
        ))

    threads = [threading.Thread(target=submit) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(created for _, created in results) == 1
    assert len({task.task_id for task, _ in results}) == 1



def test_worker_cannot_release_lease_while_task_is_running(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={"round_id": 1})
    claimed = queue.claim_next("worker-1", lease_seconds=120)
    assert claimed and claimed.task_id == task.task_id
    assert queue.release_worker("worker-1") is False
    assert queue.claim_next("worker-2", lease_seconds=120) is None


def test_delayed_task_does_not_block_ready_fifo_tasks(tmp_path):
    from datetime import datetime, timedelta, timezone

    queue = make_queue(tmp_path)
    delayed, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a",
        aggregate_id="_job_data_fetch:draft-1:1",
        payload={"method": "_job_data_fetch", "args": ["draft-1", 1]},
        available_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    ready, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-b",
        aggregate_id="2", payload={"round_id": 2},
    )

    claimed = queue.claim_next("worker-1")
    assert claimed is not None and claimed.task_id == ready.task_id
    assert queue.finish(claimed.task_id, "worker-1", result={"ok": True})

    conn = queue._connect()
    try:
        conn.execute(
            "UPDATE global_task_queue SET available_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (delayed.task_id,),
        )
    finally:
        conn.close()

    due = queue.claim_next("worker-1")
    assert due is not None and due.task_id == delayed.task_id


def test_idle_worker_releases_global_lease_for_failover(tmp_path):
    queue = make_queue(tmp_path)
    assert queue.claim_next("idle-worker") is None

    task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-next",
        aggregate_id="9", payload={"round_id": 9},
    )
    claimed = queue.claim_next("replacement-worker")
    assert claimed is not None
    assert claimed.task_id == task.task_id


def test_concurrent_initialize_migrates_legacy_queue_schema_once(tmp_path):
    import sqlite3

    from src.core.task_queue import GlobalTaskQueue

    db_path = tmp_path / "shared" / "system.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE global_task_queue ("
            "task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, tenant_id TEXT NOT NULL, "
            "aggregate_id TEXT, payload_json TEXT NOT NULL, status TEXT NOT NULL, "
            "created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, "
            "attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 1, "
            "lease_owner TEXT, lease_expires_at TEXT, result_json TEXT, "
            "error_code TEXT, error_message TEXT)"
        )
        conn.execute(
            "INSERT INTO global_task_queue(task_id,task_type,tenant_id,payload_json,status,created_at) "
            "VALUES('legacy','round_run','tenant-a','{}','queued','2026-01-01T00:00:00+00:00')"
        )

    queue = GlobalTaskQueue(db_path)
    errors = []

    def initialize():
        try:
            queue.initialize()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=initialize) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    migrated = queue.get("legacy")
    assert migrated is not None
    assert migrated.queue_order == 1
    assert migrated.available_at == migrated.created_at




def test_queue_listing_keeps_active_tasks_ahead_of_recent_history(tmp_path):
    queue = make_queue(tmp_path)
    active, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="active",
        payload={"round_id": 1},
    )
    historical_ids = []
    for index in range(3):
        task, _ = queue.enqueue(
            task_type="round_run", tenant_id="tenant-b",
            aggregate_id=f"done-{index}", payload={"round_id": index + 2},
        )
        historical_ids.append(task.task_id)

    conn = queue._connect()
    try:
        conn.executemany(
            "UPDATE global_task_queue SET status='succeeded' WHERE task_id=?",
            [(task_id,) for task_id in historical_ids],
        )
    finally:
        conn.close()

    listed = queue.list(limit=2)
    assert listed[0].task_id == active.task_id
    assert listed[0].status == "queued"


def test_startup_recovery_interrupts_previous_process_tasks_immediately(tmp_path):
    queue = make_queue(tmp_path)
    running, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="running",
        payload={"round_id": 1},
    )
    queued, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-b", aggregate_id="queued",
        payload={"round_id": 2},
    )
    claimed = queue.claim_next("previous-process", lease_seconds=120)
    assert claimed is not None and claimed.task_id == running.task_id
    result = queue.recover_orphaned_running()
    assert result == {"interrupted": 1}
    assert queue.get(running.task_id).status == "interrupted"
    assert queue.get(queued.task_id).status == "queued"
    next_task = queue.claim_next("new-process")
    assert next_task is not None and next_task.task_id == queued.task_id


def test_queue_list_skips_corrupt_payload_and_logs_it(tmp_path, caplog):
    queue = make_queue(tmp_path)
    good, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="good",
        payload={"round_id": 1},
    )
    conn = queue._connect()
    try:
        conn.execute(
            "INSERT INTO global_task_queue(queue_order,task_id,task_type,tenant_id,aggregate_id,"
            "payload_json,available_at,status,created_at) "
            "VALUES(999,'corrupt','round_run','tenant-a','bad','not-json',"
            "'2026-01-01T00:00:00+00:00','failed','2026-01-01T00:00:00+00:00')"
        )
    finally:
        conn.close()
    listed = queue.list(limit=10)
    assert [task.task_id for task in listed] == [good.task_id]
    assert "跳过无法解码的队列记录 task_id=corrupt" in caplog.text


def test_queue_rejects_unsupported_retry_count(tmp_path):
    import pytest
    queue = make_queue(tmp_path)
    with pytest.raises(ValueError, match="不自动重试"):
        queue.enqueue(
            task_type="round_run", tenant_id="tenant-a", payload={}, max_attempts=2,
        )

def test_queue_history_pagination_is_complete_and_disjoint(tmp_path):
    queue = make_queue(tmp_path)
    task_ids = []
    for index in range(47):
        task, _ = queue.enqueue(
            task_type="round_run", tenant_id=f"tenant-{index % 3}",
            aggregate_id=f"history-{index}", payload={"round_id": index},
        )
        task_ids.append(task.task_id)
    conn = queue._connect()
    try:
        conn.executemany(
            "UPDATE global_task_queue SET status='succeeded' WHERE task_id=?",
            [(task_id,) for task_id in task_ids],
        )
    finally:
        conn.close()

    first, total = queue.list_page(scope="history", page=1, page_size=20)
    second, second_total = queue.list_page(scope="history", page=2, page_size=20)
    third, third_total = queue.list_page(scope="history", page=3, page_size=20)
    assert total == second_total == third_total == 47
    assert [len(first), len(second), len(third)] == [20, 20, 7]
    collected = [task.task_id for task in first + second + third]
    assert len(collected) == len(set(collected)) == 47
    assert set(collected) == set(task_ids)


def test_queue_active_page_keeps_fifo_order(tmp_path):
    queue = make_queue(tmp_path)
    tasks = [
        queue.enqueue(task_type="round_run", tenant_id="a", payload={"n": i})[0]
        for i in range(4)
    ]
    page, total = queue.list_page(scope="active", page=1, page_size=10)
    assert total == 4
    assert [task.task_id for task in page] == [task.task_id for task in tasks]



def test_queue_history_can_filter_by_task_type(tmp_path):
    queue = make_queue(tmp_path)
    round_task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="round",
        payload={"round_id": 1},
    )
    scheduler_task, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a", aggregate_id="daily",
        payload={"method": "_job_daily_ops"},
    )
    conn = queue._connect()
    try:
        conn.executemany(
            "UPDATE global_task_queue SET status='succeeded' WHERE task_id=?",
            [(round_task.task_id,), (scheduler_task.task_id,)],
        )
    finally:
        conn.close()

    rounds, rounds_total = queue.list_page(scope="history", task_type="round_run")
    scheduler, scheduler_total = queue.list_page(scope="history", task_type="scheduler_job")
    all_items, all_total = queue.list_page(scope="history")
    assert rounds_total == 1 and [item.task_id for item in rounds] == [round_task.task_id]
    assert scheduler_total == 1 and [item.task_id for item in scheduler] == [scheduler_task.task_id]
    assert all_total == 2 and {item.task_id for item in all_items} == {round_task.task_id, scheduler_task.task_id}


def test_queue_status_counts_are_aggregated_by_tenant(tmp_path):
    queue = make_queue(tmp_path)
    queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={"n": 1})
    running_a, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={"n": 2})
    failed_b, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-b", payload={"n": 3})
    interrupted_b, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-b", payload={"n": 4})

    conn = queue._connect()
    try:
        conn.executemany(
            "UPDATE global_task_queue SET status=? WHERE task_id=?",
            [
                ("running", running_a.task_id),
                ("failed", failed_b.task_id),
                ("interrupted", interrupted_b.task_id),
            ],
        )
    finally:
        conn.close()

    active = queue.status_counts(scope="active")
    failed = queue.status_counts(scope="failed")

    assert active["total"] == 2
    assert active["by_status"] == {"queued": 1, "running": 1}
    assert active["by_tenant"] == {
        "tenant-a": {"queued": 1, "running": 1},
    }
    assert failed["total"] == 2
    assert failed["by_tenant"] == {
        "tenant-b": {"failed": 1, "interrupted": 1},
    }


def test_failed_queue_page_can_filter_task_type(tmp_path):
    queue = make_queue(tmp_path)
    round_task, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", payload={"kind": "round"},
    )
    scheduler_task, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a", payload={"kind": "scheduler"},
    )

    conn = queue._connect()
    try:
        conn.executemany(
            "UPDATE global_task_queue SET status='failed' WHERE task_id=?",
            [(round_task.task_id,), (scheduler_task.task_id,)],
        )
    finally:
        conn.close()

    items, total = queue.list_page(scope="failed", task_type="round_run")
    assert total == 1
    assert [item.task_id for item in items] == [round_task.task_id]


def test_failed_queue_page_filters_by_tenant_and_error_keyword(tmp_path):
    queue = make_queue(tmp_path)
    target, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", payload={"kind": "target"},
    )
    other_tenant, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-b", payload={"kind": "other-tenant"},
    )
    other_reason, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", payload={"kind": "other-reason"},
    )
    conn = queue._connect()
    try:
        conn.executemany(
            "UPDATE global_task_queue SET status='failed', error_message=? WHERE task_id=?",
            [
                ("平台登录失效，需要重新扫码", target.task_id),
                ("平台登录失效，需要重新扫码", other_tenant.task_id),
                ("内容校验失败", other_reason.task_id),
            ],
        )
    finally:
        conn.close()

    items, total = queue.list_page(
        scope="failed", tenant_id="tenant-a", error_keyword="登录失效",
    )
    assert total == 1
    assert [item.task_id for item in items] == [target.task_id]


def test_history_page_filters_created_date_range(tmp_path):
    queue = make_queue(tmp_path)
    first, _ = queue.enqueue(task_type="scheduler_job", tenant_id="a", payload={"n": 1})
    second, _ = queue.enqueue(task_type="scheduler_job", tenant_id="a", payload={"n": 2})
    for task in (first, second):
        claimed = queue.claim_next("worker-1")
        assert claimed is not None and claimed.task_id == task.task_id
        assert queue.fail(task.task_id, "worker-1", error_code="failed", error_message="test")
    conn = queue._connect()
    try:
        conn.execute(
            "UPDATE global_task_queue SET created_at='2026-10-30T12:00:00' WHERE task_id=?",
            (first.task_id,),
        )
        conn.execute(
            "UPDATE global_task_queue SET created_at='2026-11-01T12:00:00' WHERE task_id=?",
            (second.task_id,),
        )
        conn.commit()
    finally:
        conn.close()

    rows, total = queue.list_page(
        scope="failed", page=1, page_size=20,
        date_from="2026-11-01", date_to="2026-11-01",
    )
    assert total == 1
    assert [row.task_id for row in rows] == [second.task_id]
    try:
        queue.list_page(scope="failed", date_from="2026-11-02", date_to="2026-11-01")
    except ValueError as exc:
        assert "date_from" in str(exc)
    else:
        raise AssertionError("reversed date range must be rejected")


def test_manual_retry_preserves_tenant_payload_and_audits_confirmation(tmp_path):
    queue = make_queue(tmp_path)
    original, _ = queue.enqueue(
        task_type="round_run",
        tenant_id="tenant-a",
        aggregate_id="round-17",
        payload={"round_id": 17, "execution_snapshot": {"platform": "xhs", "account_id": "a-1"}},
    )
    claimed = queue.claim_next("worker-1")
    assert claimed is not None and claimed.task_id == original.task_id
    assert queue.fail(
        original.task_id, "worker-1",
        error_code="network_timeout", error_message="请求超时，平台状态待核实",
    )

    retry, created = queue.retry_after_manual_verification(
        original.task_id,
        confirmed_by="operator",
        confirmation_note="已在平台后台核实，该内容没有创建或发布记录",
        confirmed_not_executed=True,
    )

    assert created is True
    assert retry.task_id != original.task_id
    assert retry.retry_of_task_id == original.task_id
    assert retry.tenant_id == original.tenant_id
    assert retry.task_type == original.task_type
    assert retry.aggregate_id == original.aggregate_id
    assert retry.payload == original.payload
    assert retry.status == "queued"
    assert retry.retry_confirmed_by == "operator"
    assert retry.retry_confirmation_note == "已在平台后台核实，该内容没有创建或发布记录"
    assert queue.get(original.task_id).status == "failed"


def test_manual_retry_is_idempotent_for_same_parent(tmp_path):
    queue = make_queue(tmp_path)
    original, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={"n": 1})
    claimed = queue.claim_next("worker-1")
    assert claimed is not None
    assert queue.fail(original.task_id, "worker-1", error_code="failed", error_message="test")

    first, created = queue.retry_after_manual_verification(
        original.task_id, confirmed_by="operator",
        confirmation_note="平台后台确认没有执行记录",
        confirmed_not_executed=True,
    )
    second, created_again = queue.retry_after_manual_verification(
        original.task_id, confirmed_by="operator",
        confirmation_note="重复请求应返回同一恢复任务",
        confirmed_not_executed=True,
    )
    assert created is True
    assert created_again is False
    assert second.task_id == first.task_id
    assert queue.list_page(scope="active")[1] == 1


def test_manual_retry_requires_confirmation_and_rejects_non_terminal_state(tmp_path):
    queue = make_queue(tmp_path)
    queued, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    try:
        queue.retry_after_manual_verification(
            queued.task_id, confirmed_by="operator",
            confirmation_note="平台后台确认没有执行记录",
            confirmed_not_executed=True,
        )
    except Exception as exc:
        assert "failed/interrupted" in str(exc)
    else:
        raise AssertionError("queued task must not be manually retried")

    assert queue.cancel(queued.task_id)
    failed, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-b", payload={})
    claimed = queue.claim_next("worker-1")
    assert claimed is not None
    assert queue.fail(failed.task_id, "worker-1", error_code="failed", error_message="test")
    try:
        queue.retry_after_manual_verification(
            failed.task_id, confirmed_by="operator",
            confirmation_note="平台后台确认没有执行记录",
            confirmed_not_executed=False,
        )
    except ValueError as exc:
        assert "必须明确确认平台侧未执行" in str(exc)
    else:
        raise AssertionError("retry without explicit confirmation must be rejected")


def test_failed_task_resolution_is_audited_idempotent_and_removed_from_todos(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="scheduler_job", tenant_id="tenant-a", payload={"job": "daily"})
    claimed = queue.claim_next("worker-1")
    assert claimed is not None and claimed.task_id == task.task_id
    assert queue.fail(task.task_id, "worker-1", error_code="job_failed", error_message="采集失败")

    unresolved, unresolved_total = queue.list_page(scope="failed", unresolved_only=True)
    assert unresolved_total == 1
    assert [row.task_id for row in unresolved] == [task.task_id]

    resolution, created = queue.resolve_failed_task(
        task.task_id, action="handled", actor="session:test-user",
        note="已检查失败原因并完成线下补录",
    )
    assert created is True
    assert resolution["action"] == "handled"
    assert resolution["actor"] == "session:test-user"
    assert resolution["note"] == "已检查失败原因并完成线下补录"

    repeated, created_again = queue.resolve_failed_task(
        task.task_id, action="handled", actor="session:test-user",
        note="重复提交不应覆盖首次审计记录",
    )
    assert created_again is False
    assert repeated == resolution

    unresolved_after, unresolved_total_after = queue.list_page(scope="failed", unresolved_only=True)
    assert unresolved_total_after == 0
    assert unresolved_after == []

    history, history_total = queue.list_page(scope="failed")
    assert history_total == 1
    assert history[0].status == "failed"
    assert history[0].resolution_action == "handled"
    assert history[0].resolution_actor == "session:test-user"
    assert history[0].resolution_note == "已检查失败原因并完成线下补录"
    assert history[0].resolution_at


def test_failed_task_resolution_rejects_active_tasks_and_conflicting_actions(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    try:
        queue.resolve_failed_task(
            task.task_id, action="ignored", actor="operator",
            note="该任务当前仍在排队不能忽略",
        )
    except Exception as exc:
        assert "failed/interrupted" in str(exc)
    else:
        raise AssertionError("active tasks must not be resolved")

    claimed = queue.claim_next("worker-1")
    assert claimed is not None
    assert queue.fail(task.task_id, "worker-1", error_code="failed", error_message="test")
    queue.resolve_failed_task(
        task.task_id, action="ignored", actor="operator",
        note="该任务属于重复告警无需再次跟进",
    )
    try:
        queue.resolve_failed_task(
            task.task_id, action="handled", actor="operator",
            note="不允许覆盖首次处理决定",
        )
    except Exception as exc:
        assert "不允许反向修改" in str(exc)
    else:
        raise AssertionError("conflicting resolution must be rejected")


def test_expired_worker_cannot_commit_after_recovery_and_manual_retry_is_required(tmp_path):
    """租约过期后旧 worker 失去写入权；恢复只能经人工核验创建新任务。"""
    queue = make_queue(tmp_path)
    original, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="round-700",
        payload={"round_id": 700, "execution_snapshot": {"platform": "xhs"}},
    )
    claimed = queue.claim_next("worker-old", lease_seconds=15)
    assert claimed is not None and claimed.task_id == original.task_id

    conn = queue._connect()
    try:
        conn.execute(
            "UPDATE global_task_queue SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (original.task_id,),
        )
        conn.execute(
            "UPDATE global_task_worker_lease SET expires_at='2000-01-01T00:00:00+00:00' WHERE singleton_id=1"
        )
    finally:
        conn.close()

    assert queue.recover_expired() == {"interrupted": 1}
    recovered = queue.get(original.task_id)
    assert recovered is not None and recovered.status == "interrupted"
    assert recovered.error_code == "worker_lost"
    conn = queue._connect()
    try:
        lease_owner = conn.execute(
            "SELECT lease_owner FROM global_task_queue WHERE task_id=?",
            (original.task_id,),
        ).fetchone()["lease_owner"]
    finally:
        conn.close()
    assert lease_owner is None

    # 即使旧进程恢复执行，也不能把已中断任务提交为成功。
    assert queue.finish(original.task_id, "worker-old", result={"published": True}) is False
    assert queue.fail(
        original.task_id, "worker-old",
        error_code="late_failure", error_message="late worker",
    ) is False
    assert queue.get(original.task_id).status == "interrupted"

    # 没有人工确认平台未执行之前，不得重新入队。
    try:
        queue.retry_after_manual_verification(
            original.task_id, confirmed_by="operator",
            confirmation_note="尚未核验平台记录",
            confirmed_not_executed=False,
        )
    except ValueError as exc:
        assert "必须明确确认平台侧未执行" in str(exc)
    else:
        raise AssertionError("recovery must not replay side-effecting task automatically")

    retry, created = queue.retry_after_manual_verification(
        original.task_id, confirmed_by="operator",
        confirmation_note="已核实平台侧没有创建或发布记录",
        confirmed_not_executed=True,
    )
    assert created is True
    assert retry.status == "queued"
    assert retry.retry_of_task_id == original.task_id
    assert retry.tenant_id == original.tenant_id
    assert retry.payload == original.payload
    assert queue.get(original.task_id).status == "interrupted"


def test_recover_expired_is_idempotent_and_preserves_nonexpired_running_task(tmp_path):
    queue = make_queue(tmp_path)
    expired, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="expired",
        payload={"round_id": 1},
    )
    active, _ = queue.enqueue(
        task_type="round_run", tenant_id="tenant-b", aggregate_id="active",
        payload={"round_id": 2},
    )
    first = queue.claim_next("worker-a", lease_seconds=15)
    assert first is not None and first.task_id == expired.task_id

    # 人为让第一条任务过期，再让新 worker 接管并领取第二条任务。
    conn = queue._connect()
    try:
        conn.execute(
            "UPDATE global_task_queue SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (expired.task_id,),
        )
        conn.execute(
            "UPDATE global_task_worker_lease SET expires_at='2000-01-01T00:00:00+00:00' WHERE singleton_id=1"
        )
    finally:
        conn.close()
    assert queue.recover_expired() == {"interrupted": 1}
    second = queue.claim_next("worker-b", lease_seconds=120)
    assert second is not None and second.task_id == active.task_id

    assert queue.recover_expired() == {"interrupted": 0}
    assert queue.get(expired.task_id).status == "interrupted"
    assert queue.get(active.task_id).status == "running"
    assert queue.finish(active.task_id, "worker-b", result={"ok": True})


def test_workers_run_different_tenants_in_parallel_but_serialize_same_tenant(tmp_path):
    queue = make_queue(tmp_path)
    first_a, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={"n": 1})
    second_a, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={"n": 2})
    first_b, _ = queue.enqueue(task_type="round_run", tenant_id="b", payload={"n": 3})

    claimed_a = queue.claim_next("worker-1", lease_seconds=120)
    claimed_b = queue.claim_next("worker-2", lease_seconds=120)
    assert claimed_a is not None and claimed_a.task_id == first_a.task_id
    assert claimed_b is not None and claimed_b.task_id == first_b.task_id
    # A second task for tenant A cannot overlap its first task.
    assert queue.claim_next("worker-3", lease_seconds=120) is None

    assert queue.finish(claimed_a.task_id, "worker-1", result={"ok": True})
    next_a = queue.claim_next("worker-3", lease_seconds=120)
    assert next_a is not None and next_a.task_id == second_a.task_id


def test_worker_lease_is_scoped_to_owner_not_global_queue(tmp_path):
    queue = make_queue(tmp_path)
    first, _ = queue.enqueue(task_type="round_run", tenant_id="a", payload={"round_id": 1})
    second, _ = queue.enqueue(task_type="round_run", tenant_id="b", payload={"round_id": 2})
    claimed = queue.claim_next("worker-1", lease_seconds=120)
    assert claimed and claimed.task_id == first.task_id
    assert queue.finish(first.task_id, "worker-1", result={"ok": True})

    # worker-1 仍持有自己的租约，不应阻塞 worker-2 处理另一个账号组。
    next_task = queue.claim_next("worker-2", lease_seconds=120)
    assert next_task and next_task.task_id == second.task_id
    assert queue.release_worker("worker-1") is True
    assert queue.release_worker("worker-2") is False


def test_reentrant_claim_does_not_drop_current_worker_lease(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    claimed = queue.claim_next("worker-a", lease_seconds=120)
    assert claimed is not None and claimed.task_id == task.task_id

    assert queue.claim_next("worker-a", lease_seconds=120) is None
    assert queue.renew(task.task_id, "worker-a", lease_seconds=120) is True
    assert queue.finish(task.task_id, "worker-a", result={"ok": True})


def test_dispatch_summary_aggregates_all_statuses_and_daily_trends(tmp_path):
    queue = make_queue(tmp_path)
    tasks = []
    for tenant, task_type in (("tenant-a", "round_run"), ("tenant-a", "scheduler_job"), ("tenant-b", "round_run")):
        task, _ = queue.enqueue(task_type=task_type, tenant_id=tenant, payload={})
        tasks.append(task)

    conn = queue._connect()
    try:
        conn.execute("UPDATE global_task_queue SET status='succeeded', started_at=created_at, finished_at=created_at WHERE task_id=?", (tasks[0].task_id,))
        conn.execute("UPDATE global_task_queue SET status='failed', started_at=created_at, finished_at=created_at WHERE task_id=?", (tasks[1].task_id,))
        conn.execute("UPDATE global_task_queue SET status='cancelled' WHERE task_id=?", (tasks[2].task_id,))
    finally:
        conn.close()

    summary = queue.dispatch_summary()
    assert summary["total"] == 3
    assert summary["by_status"] == {
        "queued": 0, "running": 0, "succeeded": 1,
        "failed": 1, "cancelled": 1, "interrupted": 0,
    }
    assert len(summary["daily"]) == 1
    assert summary["daily"][0]["total"] == 3
    assert summary["daily"][0]["succeeded"] == 1
    assert summary["daily"][0]["failed"] == 1
    assert summary["average_duration_seconds"] is not None

    scoped = queue.dispatch_summary(tenant_id="tenant-a", task_type="round_run")
    assert scoped["total"] == 1
    assert scoped["by_status"]["succeeded"] == 1
    assert scoped["filters"]["tenant_id"] == "tenant-a"


def test_dispatch_summary_rejects_reversed_date_range(tmp_path):
    queue = make_queue(tmp_path)
    import pytest
    with pytest.raises(ValueError, match="date_from"):
        queue.dispatch_summary(date_from="2026-11-03", date_to="2026-11-02")


def test_dispatch_summary_can_filter_by_status(tmp_path):
    queue = make_queue(tmp_path)
    queued, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    completed, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    conn = queue._connect()
    try:
        conn.execute("UPDATE global_task_queue SET status='succeeded' WHERE task_id=?", (completed.task_id,))
    finally:
        conn.close()

    summary = queue.dispatch_summary(status="queued")
    assert summary["total"] == 1
    assert summary["by_status"]["queued"] == 1
    assert summary["filters"]["status"] == "queued"


def test_cancel_and_retry_admin_actions_are_audited(tmp_path):
    queue = make_queue(tmp_path)
    cancelled, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    assert queue.cancel(cancelled.task_id, actor="session:test-operator")
    audit = queue.list_admin_audit(cancelled.task_id)
    assert len(audit) == 1
    assert audit[0]["action"] == "cancel"
    assert audit[0]["actor"] == "session:test-operator"

    failed, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    conn = queue._connect()
    try:
        conn.execute("UPDATE global_task_queue SET status='failed' WHERE task_id=?", (failed.task_id,))
    finally:
        conn.close()
    retry, created = queue.retry_after_manual_verification(
        failed.task_id, confirmed_by="session:test-operator",
        confirmation_note="已核验平台无执行记录", confirmed_not_executed=True,
    )
    assert created
    audit = queue.list_admin_audit(failed.task_id)
    assert audit[0]["action"] == "retry"
    assert audit[0]["actor"] == "session:test-operator"
    assert audit[0]["details"]["retry_task_id"] == retry.task_id


def test_resolve_action_is_recorded_in_admin_audit(tmp_path):
    queue = make_queue(tmp_path)
    task, _ = queue.enqueue(task_type="round_run", tenant_id="tenant-a", payload={})
    conn = queue._connect()
    try:
        conn.execute("UPDATE global_task_queue SET status='failed' WHERE task_id=?", (task.task_id,))
    finally:
        conn.close()
    queue.resolve_failed_task(task.task_id, action="handled", actor="session:auditor", note="已检查并修复相关配置")
    audit = queue.list_admin_audit(task.task_id)
    assert len(audit) == 1
    assert audit[0]["action"] == "resolve_handled"
    assert audit[0]["actor"] == "session:auditor"
