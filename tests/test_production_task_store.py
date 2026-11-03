"""Tests for the independent production-task lifecycle."""
import asyncio
import sqlite3

import pytest

from src.core.production_line import ProductionLineRunner
from src.core.production_task_store import ProductionTaskError, ProductionTaskStore


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE spiral_rounds(
        id INTEGER PRIMARY KEY, master_plan_version_id INTEGER, sub_plan_id TEXT,
        sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER,
        sub_plan_snapshot TEXT, sub_plan_stage_snapshot TEXT,
        production_line_version_id INTEGER
    )""")
    conn.execute(
        """INSERT INTO spiral_rounds(
            id,master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id,
            sub_plan_snapshot,sub_plan_stage_snapshot,production_line_version_id
        ) VALUES(7,11,'sub-a',13,17,'{"title":"frozen"}','{"stage":"frozen"}',77)"""
    )
    conn.executescript("""
        CREATE TABLE sub_plans(id TEXT PRIMARY KEY, platform TEXT);
        INSERT INTO sub_plans VALUES('sub-a', 'xhs');
        CREATE TABLE sub_plan_versions(id INTEGER PRIMARY KEY, sub_plan_id TEXT);
        INSERT INTO sub_plan_versions VALUES(13, 'sub-a');
        CREATE TABLE sub_plan_stages(
            id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER, stage_key TEXT
        );
        INSERT INTO sub_plan_stages VALUES(17, 13, 'step-stable-1');
    """)
    conn.execute("""CREATE TABLE contents(
        id TEXT PRIMARY KEY, production_task_id TEXT, round_id INTEGER, platform TEXT
    )""")
    conn.executescript("""
        CREATE TABLE production_tasks (
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, round_id INTEGER,
            master_plan_version_id INTEGER, sub_plan_id TEXT, sub_plan_version_id INTEGER,
            strategy_step_id TEXT, platform TEXT, task_ordinal INTEGER,
            production_line_version_id INTEGER, status TEXT,
            attempt_count INTEGER, max_attempts INTEGER, content_id TEXT,
            input_snapshot_json TEXT, error_code TEXT, error_message TEXT,
            available_at TEXT, started_at TEXT, finished_at TEXT,
            lease_expires_at TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE production_task_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, attempt_no INTEGER,
            status TEXT, worker_id TEXT, started_at TEXT, finished_at TEXT,
            error_code TEXT, error_message TEXT, output_content_id TEXT,
            UNIQUE(task_id, attempt_no)
        );
    """)
    yield ProductionTaskStore(conn)
    conn.close()


def make_task(store, **overrides):
    values = dict(
        round_id=7, master_plan_version_id=11, sub_plan_id="sub-a",
        sub_plan_version_id=13, strategy_step_id="step-stable-1",
        platform="xhs", task_ordinal=1, idempotency_key="round-7:step-1:xhs:1",
        input_snapshot={"topic":"topic-a"}, max_attempts=3,
    )
    values.update(overrides)
    return store.create_task(**values)


def test_idempotent_create_returns_same_task(store):
    first = make_task(store)
    second = make_task(store)
    assert first["id"] == second["id"]
    assert store.step_progress("step-stable-1")["total"] == 1


def test_one_strategy_step_can_create_multiple_independent_tasks(store):
    tasks = store.create_tasks_for_step(
        round_id=7, master_plan_version_id=11, sub_plan_id="sub-a",
        sub_plan_version_id=13, strategy_step_id="step-stable-1",
        tasks=[
            {"platform":"xhs","idempotency_key":"step-1:xhs:1","input_snapshot":{"topic":"a"}},
            {"platform":"xhs","idempotency_key":"step-1:xhs:2","input_snapshot":{"topic":"b"}},
        ],
    )
    assert len(tasks) == 2
    assert tasks[0]["id"] != tasks[1]["id"]
    assert store.step_progress("step-stable-1")["total"] == 2
    assert store.round_progress(7)["active"] == 2


def test_task_inherits_and_freezes_the_round_production_line_version(store):
    store.conn.execute(
        "UPDATE spiral_rounds SET production_line_version_id=77 WHERE id=7"
    )
    task = make_task(store)
    assert task["production_line_version_id"] == 77
    assert task["input_snapshot"]["round"]["production_line_version_id"] == 77
    with pytest.raises(ProductionTaskError, match="版本与生产轮次绑定"):
        make_task(store, idempotency_key="wrong-line-version", production_line_version_id=78)


def test_new_task_rejects_round_without_production_line_version(store):
    store.conn.execute(
        "UPDATE spiral_rounds SET production_line_version_id=NULL WHERE id=7"
    )
    with pytest.raises(ProductionTaskError, match="未绑定生产线版本"):
        make_task(store, idempotency_key="missing-line-version")


def test_create_requires_round_source_to_match(store):
    with pytest.raises(ProductionTaskError, match="来源"):
        make_task(store, master_plan_version_id=99)

def test_create_requires_the_round_bound_strategy_step(store):
    with pytest.raises(ProductionTaskError, match="策略步骤"):
        make_task(store, strategy_step_id="different-step")


def test_create_freezes_round_and_input_snapshots(store):
    task = make_task(store)
    assert task["input_snapshot"]["round"]["sub_plan_snapshot"] == '{"title":"frozen"}'
    assert task["input_snapshot"]["task_input"] == {"topic":"topic-a"}


def test_idempotency_key_cannot_be_reused_for_another_platform(store):
    make_task(store)
    with pytest.raises(ProductionTaskError, match="平台"):
        make_task(store, platform="douyin")


def test_task_can_fail_and_retry_without_creating_a_duplicate(store):
    task = make_task(store)
    store.claim_task(task["id"], worker_id="worker-a")
    failed = store.fail_task(task["id"], error_code="llm_timeout", error_message="timeout")
    assert failed["status"] == "pending"
    retried = store.claim_task(task["id"], worker_id="worker-b")
    assert retried["attempt_count"] == 2
    assert len(store.conn.execute(
        "SELECT * FROM production_task_attempts WHERE task_id=?", (task["id"],)
    ).fetchall()) == 2


def test_terminal_failure_does_not_block_other_tasks(store):
    first = make_task(store, idempotency_key="task-a", max_attempts=1)
    second = make_task(store, idempotency_key="task-b", task_ordinal=2)
    store.claim_task(first["id"], worker_id="worker-a")
    store.fail_task(first["id"], error_code="bad_input", error_message="invalid", retryable=False)
    assert store.get_task(first["id"])["status"] == "failed"
    assert store.claim_task(second["id"], worker_id="worker-b")["status"] == "running"


def test_success_requires_content_and_updates_step_progress(store):
    task = make_task(store)
    store.claim_task(task["id"], worker_id="worker-a")
    with pytest.raises(ProductionTaskError, match="内容成品"):
        store.complete_task(task["id"], content_id="")
    store.conn.execute(
        "INSERT INTO contents(id,round_id,platform) VALUES('content-1',7,'xhs')"
    )
    store.complete_task(task["id"], content_id="content-1")
    assert store.conn.execute(
        "SELECT production_task_id FROM contents WHERE id='content-1'"
    ).fetchone()[0] == task["id"]
    assert store.step_progress("step-stable-1") == {
        "total":1, "succeeded":1, "failed":0, "cancelled":0, "active":0
    }


def test_success_rejects_artifact_from_another_round_or_platform(store):
    task = make_task(store)
    store.claim_task(task["id"], worker_id="worker-a")
    store.conn.execute(
        "INSERT INTO contents(id,round_id,platform) VALUES('wrong-round',8,'xhs')"
    )
    with pytest.raises(ProductionTaskError, match="轮次"):
        store.complete_task(task["id"], content_id="wrong-round")
    store.conn.execute(
        "INSERT INTO contents(id,round_id,platform) VALUES('wrong-platform',7,'douyin')"
    )
    with pytest.raises(ProductionTaskError, match="平台"):
        store.complete_task(task["id"], content_id="wrong-platform")
def test_max_attempts_can_only_be_extended_explicitly(store):
    task = make_task(store, max_attempts=1)
    store.claim_task(task["id"], worker_id="worker-a")
    store.fail_task(task["id"], error_code="timeout", error_message="timeout")
    with pytest.raises(ProductionTaskError, match="最大尝试次数"):
        store.retry_task(task["id"])
    extended = store.extend_attempts(task["id"], max_attempts=2)
    assert extended["max_attempts"] == 2
    assert store.retry_task(task["id"])["status"] == "pending"


def test_existing_artifact_can_be_found_after_worker_crash(store):
    task = make_task(store)
    store.conn.execute(
        "INSERT INTO contents(id,production_task_id,round_id,platform) VALUES('content-recovered',?,7,'xhs')",
        (task["id"],),
    )
    assert store.get_content_for_task(task["id"])["id"] == "content-recovered"


def test_batch_runner_keeps_partial_success_when_one_task_fails(store):
    tasks = store.create_tasks_for_step(
        round_id=7, master_plan_version_id=11, sub_plan_id="sub-a",
        sub_plan_version_id=13, strategy_step_id="step-stable-1",
        tasks=[
            {"platform":"xhs","idempotency_key":"runner:xhs"},
            {"platform":"xhs","idempotency_key":"runner:xhs:2"},
        ],
    )
    async def producer(task):
        if task["task_ordinal"] == 1:
            raise RuntimeError("temporary generation error")
        content_id = "content-runner-" + str(task["task_ordinal"])
        store.conn.execute(
            "INSERT INTO contents(id,round_id,platform) VALUES(?,7,'xhs')", (content_id,)
        )
        return content_id

    runner = ProductionLineRunner(store, worker_id="worker-batch")
    report = asyncio.run(runner.run_batch([row["id"] for row in tasks], producer))
    assert report["total"] == 2
    assert report["pending"] == 1
    assert report["succeeded"] == 1
    assert store.round_progress(7)["active"] == 1


def test_cancel_and_expired_lease_recovery(store):
    task = make_task(store)
    store.claim_task(task["id"], worker_id="worker-a", lease_seconds=30)
    assert store.recover_expired_tasks(
        now="2999-01-01T00:00:00+00:00", round_id=8
    ) == []
    recovered = store.recover_expired_tasks(
        now="2999-01-01T00:00:00+00:00", round_id=7
    )
    assert recovered == [task["id"]]
    assert store.get_task(task["id"])["status"] == "failed"
    assert store.get_task(task["id"])["error_code"] == "lease_expired_requires_review"
    store.cancel_task(task["id"], reason="stop")
    assert store.get_task(task["id"])["status"] == "cancelled"


def test_create_task_does_not_commit_a_callers_transaction(store):
    conn = store.conn
    conn.execute("BEGIN")
    conn.execute("INSERT INTO contents(id,round_id,platform) VALUES('uncommitted-probe',7,'xhs')")

    task = make_task(store)

    assert conn.in_transaction
    assert store.get_task(task["id"])["status"] == "pending"
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM contents WHERE id='uncommitted-probe'"
    ).fetchone()[0] == 0


def test_complete_task_keeps_artifact_and_task_in_callers_transaction(store):
    task = make_task(store)
    store.claim_task(task["id"], worker_id="worker-a")
    conn = store.conn
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO contents(id,round_id,platform) VALUES('atomic-content',7,'xhs')"
    )

    store.complete_task(task["id"], content_id="atomic-content")

    assert conn.in_transaction
    assert store.get_task(task["id"])["status"] == "succeeded"
    conn.rollback()
    assert store.get_task(task["id"])["status"] == "running"
    assert conn.execute(
        "SELECT COUNT(*) FROM contents WHERE id='atomic-content'"
    ).fetchone()[0] == 0
