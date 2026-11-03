"""Regression: production wraps tenant connections in LockedConnection and sync write
routes in a request-level `with conn` (TransactionalAPIRoute). connection_transaction must
join that outer context instead of raising, and file cleanup must wait for the commit."""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.api import main as api_main
from src.core.db import LockedConnection, connection_transaction


def _locked(raw):
    raw.row_factory = sqlite3.Row
    return LockedConnection(raw)


def test_connection_transaction_joins_outer_locked_context_and_rolls_back_together(tmp_path):
    conn = _locked(sqlite3.connect(tmp_path / "t.db", check_same_thread=False))
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()

    with conn:  # request-level context, no write started yet
        with connection_transaction(conn, immediate=True):
            conn.execute("INSERT INTO t VALUES (1)")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1

    with pytest.raises(RuntimeError):
        with conn:
            with connection_transaction(conn, immediate=True):
                conn.execute("INSERT INTO t VALUES (2)")
                raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1

    # Inner failure that the route turns into an HTTP error still rolls back the outer write.
    with pytest.raises(ValueError):
        with conn:
            conn.execute("INSERT INTO t VALUES (3)")
            with connection_transaction(conn, immediate=True):
                raise ValueError("blocked")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def _image_content(cfg, conn, seed):
    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, draft_status="draft_saved", fetch_status=None)
    content_id = conn.execute("SELECT content_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0]
    out = cfg.resolve_path(cfg.paths.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    image = out / f"{content_id}.png"
    image.write_bytes(b"x")
    conn.execute("UPDATE contents SET image_paths=? WHERE id=?", (json.dumps([str(image)]), content_id))
    conn.commit()
    return round_id, content_id, draft_id, image


@pytest.fixture()
def locked_client(cfg, conn, seed, test_auth):
    from fastapi.testclient import TestClient

    saved = dict(api_main._g_ctx)
    locked = LockedConnection(conn)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({"cfg": cfg, "conn": locked})
    app = api_main._build_app()
    try:
        yield test_auth(TestClient(app, base_url="http://127.0.0.1")), locked
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


def test_delete_round_api_with_locked_connection(locked_client, cfg, conn, seed, monkeypatch):
    tc, locked = locked_client
    monkeypatch.setattr(api_main, "_round_task_active", lambda *_a, **_k: False)
    round_id, content_id, draft_id, image = _image_content(cfg, conn, seed)
    pend_round, pend_content, pend_draft, pend_image = _image_content(cfg, conn, seed)
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id,status,attempted_at) VALUES (?,'pending_verify','2026-11-02T12:00:00')",
        (pend_draft,),
    )
    conn.commit()

    blocked = tc.delete(f"/api/rounds/{pend_round}")
    assert blocked.status_code == 409, blocked.text
    assert pend_image.exists()
    assert conn.execute("SELECT COUNT(*) FROM contents WHERE round_id=?", (pend_round,)).fetchone()[0] == 1

    ok = tc.delete(f"/api/rounds/{round_id}")
    assert ok.status_code == 200, ok.text
    assert ok.json()["files_cleanup_deferred"] is True
    assert conn.execute("SELECT COUNT(*) FROM spiral_rounds WHERE id=?", (round_id,)).fetchone()[0] == 0
    assert not image.exists()  # BackgroundTasks ran after the request transaction committed

    single = tc.post("/api/contents/delete", json={"ids": [pend_content]})
    assert single.status_code == 200, single.text
    assert single.json()["blocked"]


def test_goal_patch_with_locked_connection(locked_client):
    tc, _ = locked_client
    base = {
        "target_metric": "followers", "target_value": 10, "metric_scope": "total",
        "baseline_value": 1, "baseline_at": "2026-11-01", "metric_period_start": "2026-11-01",
        "metric_period_end": "2026-11-30", "data_source": "manual",
    }
    root = tc.post("/api/goals", json={**base, "goal_text": "root", "goal_level": "north_star"})
    assert root.status_code == 200, root.text
    child = tc.post("/api/goals", json={**base, "goal_text": "c", "goal_level": "platform",
                                         "target_platform": "xhs", "parent_goal_id": root.json()["id"]})
    assert child.status_code == 200, child.text
    assert tc.patch(f"/api/goals/{root.json()['id']}", json={"goal_level": "platform"}).status_code == 422
    edited = tc.patch(f"/api/goals/{child.json()['id']}", json={"goal_text": "c2"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["goal_text"] == "c2"

def test_connection_transaction_serializes_multi_statement_write_against_other_thread(tmp_path):
    import threading

    conn = _locked(sqlite3.connect(tmp_path / "serialized.db", check_same_thread=False))
    conn.execute("CREATE TABLE events (value TEXT NOT NULL)")
    conn.commit()
    first_statement_done = threading.Event()
    allow_first_to_finish = threading.Event()
    second_statement_done = threading.Event()
    errors = []

    def first_writer():
        try:
            with connection_transaction(conn, immediate=True):
                conn.execute("INSERT INTO events VALUES ('first-a')")
                first_statement_done.set()
                assert allow_first_to_finish.wait(2)
                conn.execute("INSERT INTO events VALUES ('first-b')")
        except BaseException as exc:
            errors.append(exc)

    def second_writer():
        try:
            assert first_statement_done.wait(2)
            conn.execute("INSERT INTO events VALUES ('second')")
            conn.commit()
            second_statement_done.set()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=first_writer)
    second = threading.Thread(target=second_writer)
    first.start()
    second.start()
    assert first_statement_done.wait(2)
    # The second thread must not be able to execute between the two statements
    # protected by the first writer's transaction-level lock.
    assert not second_statement_done.wait(0.05)
    allow_first_to_finish.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert [row[0] for row in conn.execute("SELECT value FROM events ORDER BY rowid")] == [
        "first-a", "first-b", "second",
    ]
