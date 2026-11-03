"""Regression tests for crash-safe production-task recovery."""
import sqlite3

import pytest

from src.core.production_task_store import ProductionTaskStore
from src.core.production_task_recovery import (
    reconcile_round_tasks,
    reconcile_expired_production_tasks,
)


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE spiral_rounds(
            id INTEGER PRIMARY KEY, master_plan_version_id INTEGER, sub_plan_id TEXT,
            sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER,
            sub_plan_snapshot TEXT, sub_plan_stage_snapshot TEXT
        );
        INSERT INTO spiral_rounds VALUES(
            7,11,'sub-a',13,17,'{}','{}'
        );
        CREATE TABLE sub_plans(id TEXT PRIMARY KEY, platform TEXT);
        INSERT INTO sub_plans VALUES('sub-a','xhs');
        CREATE TABLE sub_plan_versions(id INTEGER PRIMARY KEY, sub_plan_id TEXT);
        INSERT INTO sub_plan_versions VALUES(13,'sub-a');
        CREATE TABLE sub_plan_stages(
            id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER, stage_key TEXT
        );
        INSERT INTO sub_plan_stages VALUES(17,13,'step-a');
        CREATE TABLE contents(
            id TEXT PRIMARY KEY, production_task_id TEXT, round_id INTEGER, platform TEXT
        );
        CREATE TABLE production_tasks(
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, round_id INTEGER,
            master_plan_version_id INTEGER, sub_plan_id TEXT, sub_plan_version_id INTEGER,
            strategy_step_id TEXT, platform TEXT, task_ordinal INTEGER, status TEXT,
            attempt_count INTEGER, max_attempts INTEGER, content_id TEXT,
            input_snapshot_json TEXT, error_code TEXT, error_message TEXT,
            available_at TEXT, started_at TEXT, finished_at TEXT,
            lease_expires_at TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE production_task_attempts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, attempt_no INTEGER,
            status TEXT, worker_id TEXT, started_at TEXT, finished_at TEXT,
            error_code TEXT, error_message TEXT, output_content_id TEXT,
            UNIQUE(task_id,attempt_no)
        );
    """)
    yield ProductionTaskStore(conn)
    conn.close()


def make_task(store, key, ordinal=1):
    return store.create_task(
        round_id=7, master_plan_version_id=11, sub_plan_id="sub-a",
        sub_plan_version_id=13, strategy_step_id="step-a", platform="xhs",
        task_ordinal=ordinal, idempotency_key=key, max_attempts=3,
    )


def test_exception_closes_only_running_tasks_and_preserves_successful_siblings(store):
    succeeded = make_task(store, "success")
    interrupted = make_task(store, "interrupted", 2)
    store.claim_task(succeeded["id"], worker_id="worker")
    store.conn.execute(
        "INSERT INTO contents(id,round_id,platform) VALUES('content-ok',7,'xhs')"
    )
    store.complete_task(succeeded["id"], content_id="content-ok")
    store.claim_task(interrupted["id"], worker_id="worker")
    outcomes = reconcile_round_tasks(
        store.conn, round_id=7, interrupted=True, error=RuntimeError("image API timed out"),
    )
    assert store.get_task(succeeded["id"])["status"] == "succeeded"
    assert store.get_task(interrupted["id"])["status"] == "failed"
    assert store.get_task(interrupted["id"])["error_code"] == "interrupted_requires_review"
    assert any(item["action"] == "manual_review_required" for item in outcomes)
    attempt = store.conn.execute(
        "SELECT status,error_code FROM production_task_attempts WHERE task_id=?",
        (interrupted["id"],),
    ).fetchone()
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == "interrupted_requires_review"


def test_reconciliation_adopts_persisted_content_without_regeneration(store):
    task = make_task(store, "artifact")
    store.claim_task(task["id"], worker_id="worker")
    store.conn.execute(
        "INSERT INTO contents(id,production_task_id,round_id,platform) VALUES('content-existing',?,7,'xhs')",
        (task["id"],),
    )
    outcomes = reconcile_round_tasks(
        store.conn, round_id=7, interrupted=True, error=RuntimeError("db write after content"),
    )
    assert store.get_task(task["id"])["status"] == "succeeded"
    assert store.get_task(task["id"])["content_id"] == "content-existing"
    assert outcomes[0]["action"] == "adopted_content"


def test_global_expired_recovery_includes_historical_rounds_and_requires_review(store):
    task = make_task(store, "expired")
    store.claim_task(task["id"], worker_id="old-worker")
    store.conn.execute(
        "UPDATE production_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
        (task["id"],),
    )
    outcomes = reconcile_expired_production_tasks(store.conn)
    assert outcomes == [{
        "task_id": task["id"], "round_id": 7, "action": "manual_review_required",
    }]
    recovered = store.get_task(task["id"])
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "lease_expired_requires_review"



def test_global_expired_sweeper_holds_write_transaction_during_reconciliation(store):
    statements = []
    store.conn.set_trace_callback(statements.append)
    task = make_task(store, "serialized-expired")
    store.claim_task(task["id"], worker_id="old-worker")
    store.conn.execute(
        "UPDATE production_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
        (task["id"],),
    )
    # End the setup write transaction so the sweeper itself must acquire its
    # BEGIN IMMEDIATE reservation before selecting expired tasks.
    store.conn.commit()
    statements.clear()

    reconcile_expired_production_tasks(store.conn)

    normalized = [statement.strip().upper() for statement in statements]
    begin_index = normalized.index("BEGIN IMMEDIATE")
    select_index = next(i for i, statement in enumerate(normalized) if "SELECT ID,ROUND_ID FROM PRODUCTION_TASKS" in statement)
    update_index = next(i for i, statement in enumerate(normalized) if "UPDATE PRODUCTION_TASKS SET STATUS=" in statement)
    commit_index = normalized.index("COMMIT")
    assert begin_index < select_index < update_index < commit_index


def test_store_expired_lease_does_not_blindly_requeue_without_artifact(store):
    task = make_task(store, "store-expired")
    store.claim_task(task["id"], worker_id="old-worker")
    store.conn.execute(
        "UPDATE production_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
        (task["id"],),
    )
    recovered = store.recover_expired_tasks(now="2020-01-01T00:00:00+00:00", round_id=7)
    assert recovered == [task["id"]]
    result = store.get_task(task["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "lease_expired_requires_review"


def test_store_expired_lease_adopts_existing_content(store):
    task = make_task(store, "store-artifact-expired")
    store.claim_task(task["id"], worker_id="old-worker")
    store.conn.execute(
        "INSERT INTO contents(id,production_task_id,round_id,platform) VALUES('store-content',?,7,'xhs')",
        (task["id"],),
    )
    store.conn.execute(
        "UPDATE production_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
        (task["id"],),
    )
    recovered = store.recover_expired_tasks(now="2020-01-01T00:00:00+00:00", round_id=7)
    assert recovered == [task["id"]]
    result = store.get_task(task["id"])
    assert result["status"] == "succeeded"
    assert result["content_id"] == "store-content"
