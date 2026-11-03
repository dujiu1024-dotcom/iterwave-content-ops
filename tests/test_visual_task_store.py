import sqlite3

import pytest

from src.core.visual_task_store import VisualTaskBusy, VisualTaskStore


def _store(tmp_path):
    db_path = tmp_path / "visual.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE visual_generation_tasks (
            task_id TEXT PRIMARY KEY, account_group TEXT NOT NULL, status TEXT NOT NULL,
            owner TEXT, lease_expires_at REAL, started_at REAL, heartbeat_at REAL,
            finished_at REAL, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE visual_generation_pages (
            task_id TEXT NOT NULL, page_index INTEGER NOT NULL, status TEXT NOT NULL,
            output_path TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
            updated_at REAL NOT NULL, PRIMARY KEY(task_id, page_index)
        );
        CREATE TABLE visual_generation_task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            from_status TEXT, to_status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL
        );
        CREATE TABLE visual_generation_account_leases (
            account_group TEXT PRIMARY KEY, task_id TEXT NOT NULL,
            owner TEXT NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL
        );
    """)
    conn.close()
    return VisualTaskStore(db_path)


def test_visual_task_lease_is_exclusive_per_account_group(tmp_path):
    store = _store(tmp_path)
    store.acquire("task-a", "account-a", owner="worker-a")
    with pytest.raises(VisualTaskBusy):
        store.acquire("task-b", "account-a", owner="worker-b")
    # A different account group remains independent.
    store.acquire("task-c", "account-b", owner="worker-b")


def test_completed_page_is_reused_without_regeneration(tmp_path):
    store = _store(tmp_path)
    output = tmp_path / "page.png"
    output.write_bytes(b"accepted image")
    store.acquire("task-a", "account-a", owner="worker-a")
    store.start_page("task-a", 0)
    store.complete_page("task-a", 0, str(output))
    assert store.completed_page("task-a", 0) == str(output)


def test_missing_completed_page_fails_closed_without_regeneration(tmp_path):
    store = _store(tmp_path)
    store.acquire("task-a", "account-a", owner="worker-a")
    store.start_page("task-a", 0)
    store.complete_page("task-a", 0, str(tmp_path / "missing.png"))
    with pytest.raises(FileNotFoundError, match="completed visual page artifact is missing"):
        store.completed_page("task-a", 0)


def test_expired_worker_lease_marks_task_interrupted(tmp_path):
    store = _store(tmp_path)
    store.acquire("task-a", "account-a", owner="worker-a", lease_seconds=1)
    conn = sqlite3.connect(tmp_path / "visual.db")
    conn.execute(
        "UPDATE visual_generation_account_leases SET expires_at=0 WHERE account_group='account-a'"
    )
    conn.commit()
    conn.close()

    store.acquire("task-b", "account-b", owner="worker-b")
    conn = sqlite3.connect(tmp_path / "visual.db")
    try:
        status = conn.execute(
            "SELECT status FROM visual_generation_tasks WHERE task_id='task-a'"
        ).fetchone()[0]
        event = conn.execute(
            "SELECT to_status,reason FROM visual_generation_task_events WHERE task_id='task-a' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert status == "interrupted"
    assert event == ("interrupted", "worker lease expired")


def test_completed_task_without_page_checkpoint_fails_closed(tmp_path):
    store = _store(tmp_path)
    owner = store.acquire("task-a", "account-a", owner="worker-a")
    store.finish("task-a", owner, "completed")
    with pytest.raises(FileNotFoundError, match="completed visual task has no page checkpoint"):
        store.completed_page("task-a", 0)
