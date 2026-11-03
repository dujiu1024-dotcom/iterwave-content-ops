"""Regression tests for transaction-scoped locking on a shared SQLite connection."""

import sqlite3
import threading
from types import SimpleNamespace

import pytest

from src.core.db import Database, LockedConnection
from src.api import main as api_main


def test_unrelated_thread_cannot_commit_another_threads_partial_write():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
    conn.commit()

    write_started = threading.Event()
    allow_writer_commit = threading.Event()
    other_commit_started = threading.Event()
    other_commit_finished = threading.Event()

    def writer():
        conn.execute("INSERT INTO items(value) VALUES ('partial')")
        write_started.set()
        assert allow_writer_commit.wait(timeout=3)
        conn.commit()

    def unrelated_request():
        assert write_started.wait(timeout=3)
        other_commit_started.set()
        conn.commit()
        other_commit_finished.set()

    writer_thread = threading.Thread(target=writer)
    other_thread = threading.Thread(target=unrelated_request)
    writer_thread.start()
    other_thread.start()

    assert other_commit_started.wait(timeout=3)
    # The unrelated request's commit must wait for the transaction owner.
    assert not other_commit_finished.wait(timeout=0.2)

    allow_writer_commit.set()
    writer_thread.join(timeout=3)
    other_thread.join(timeout=3)

    assert not writer_thread.is_alive()
    assert not other_thread.is_alive()
    assert other_commit_finished.is_set()
    assert conn.execute("SELECT value FROM items").fetchall()[0][0] == "partial"
    conn.close()


def test_transaction_context_holds_lock_across_read_check_write():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
    conn.execute("INSERT INTO records(status) VALUES ('draft')")
    conn.commit()

    transaction_started = threading.Event()
    allow_transaction_finish = threading.Event()
    competing_read_finished = threading.Event()

    def business_operation():
        with conn:
            row = conn.execute("SELECT status FROM records WHERE id=1").fetchone()
            assert row[0] == "draft"
            conn.execute("UPDATE records SET status='saved' WHERE id=1")
            transaction_started.set()
            assert allow_transaction_finish.wait(timeout=3)

    def unrelated_request():
        assert transaction_started.wait(timeout=3)
        conn.execute("SELECT status FROM records WHERE id=1").fetchone()
        competing_read_finished.set()

    first = threading.Thread(target=business_operation)
    second = threading.Thread(target=unrelated_request)
    first.start()
    second.start()

    assert transaction_started.wait(timeout=3)
    assert not competing_read_finished.wait(timeout=0.2)
    allow_transaction_finish.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert competing_read_finished.is_set()
    assert conn.execute("SELECT status FROM records WHERE id=1").fetchone()[0] == "saved"
    conn.close()


def test_context_rolls_back_content_when_audit_insert_fails():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE contents (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE audit (id INTEGER PRIMARY KEY, body TEXT NOT NULL "
        "CHECK(length(body) < 10))"
    )
    conn.execute("INSERT INTO contents(id, body) VALUES (1, 'original')")
    conn.commit()

    try:
        with conn:
            conn.execute("UPDATE contents SET body='partially changed' WHERE id=1")
            # Simulate audit persistence failing after the business row was updated.
            conn.execute("INSERT INTO audit(body) VALUES ('this audit row is too long')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected audit constraint failure")

    assert conn.execute("SELECT body FROM contents WHERE id=1").fetchone()[0] == "original"
    assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0
    conn.close()


def test_nested_transaction_scope_does_not_commit_early():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE events (value TEXT NOT NULL)")
    conn.commit()

    with conn:
        conn.execute("INSERT INTO events(value) VALUES ('outer')")
        try:
            with conn:
                conn.execute("INSERT INTO events(value) VALUES ('inner')")
                conn.commit()  # Must defer to the outermost transaction scope.
                raise RuntimeError("force nested rollback")
        except RuntimeError:
            pass
        conn.execute("INSERT INTO events(value) VALUES ('after-inner-error')")

    # A failed nested scope marks the complete outer transaction rollback-only.
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()


def test_caught_sql_error_marks_request_transaction_rollback_only():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute(
        "CREATE TABLE records (value TEXT NOT NULL UNIQUE CHECK(length(value) < 8)"
        ")"
    )
    conn.commit()

    with conn:
        conn.execute("INSERT INTO records(value) VALUES ('first')")
        try:
            conn.execute("INSERT INTO records(value) VALUES ('too-long-value')")
        except sqlite3.IntegrityError:
            # Even if application code catches the SQL exception, the request must roll back.
            pass
        conn.execute("INSERT INTO records(value) VALUES ('third')")

    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
    conn.close()


def test_pending_verify_draft_can_be_manually_reconciled(seed, conn):
    from src.core.state_machine import DraftStateMachine

    saved_round_id = seed.round(status="producing")
    retry_round_id = seed.round(status="producing")
    saved_id = seed.draft(saved_round_id, draft_status="draft_pending", fetch_status=None)
    retry_id = seed.draft(retry_round_id, draft_status="draft_pending", fetch_status=None)
    machine = DraftStateMachine(conn)
    for draft_id in (saved_id, retry_id):
        conn.execute(
            "INSERT INTO draft_save_verifications(draft_id,status,attempted_at) "
            "VALUES (?, 'pending_verify', '2026-11-02 10:00:00')",
            (draft_id,),
        )
    conn.commit()

    # 已在平台找到：推进真实草稿状态并完成核实账本。
    assert machine.transition(
        saved_id, "draft_saved", reason="operator verified platform draft exists",
        extra_updates={"saved_content_hash": "verified-hash", "error_msg": None},
    )
    conn.execute(
        "UPDATE draft_save_verifications SET status='verified_saved', verified_at=?, note=? WHERE draft_id=?",
        ("2026-11-02 10:05:00", "platform draft exists", saved_id),
    )
    # 平台不存在：保留 draft_pending，解除自动重试保护，允许用户重新存草稿。
    conn.execute(
        "UPDATE draft_save_verifications SET status='verified_missing', verified_at=?, note=? WHERE draft_id=?",
        ("2026-11-02 10:05:00", "platform draft absent", retry_id),
    )
    conn.commit()
    statuses = {
        row["id"]: row["status"]
        for row in conn.execute(
            "SELECT id, status FROM drafts WHERE id IN (?, ?)", (saved_id, retry_id),
        ).fetchall()
    }
    verification = {
        row["draft_id"]: row["status"]
        for row in conn.execute(
            "SELECT draft_id, status FROM draft_save_verifications WHERE draft_id IN (?, ?)",
            (saved_id, retry_id),
        ).fetchall()
    }
    assert statuses == {saved_id: "draft_saved", retry_id: "draft_pending"}
    assert verification == {saved_id: "verified_saved", retry_id: "verified_missing"}


def test_pending_verify_content_cannot_be_deleted(cfg, conn, seed):
    from src.core.content_admin import delete_contents

    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(round_id, draft_status="draft_pending", fetch_status=None)
    conn.execute(
        "INSERT INTO draft_save_verifications(draft_id,status,attempted_at) "
        "VALUES (?, 'pending_verify', '2026-11-02 10:00:00')",
        (draft_id,),
    )
    conn.commit()
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0]

    result = delete_contents(cfg, conn, [content_id])
    assert result["deleted"] == []
    assert result["blocked"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0] == "draft_pending"
    assert conn.execute(
        "SELECT status FROM draft_save_verifications WHERE draft_id=?", (draft_id,),
    ).fetchone()[0] == "pending_verify"


def test_transaction_rolls_back_all_statements_on_exception():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE events (value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="audit failed"):
        with conn.transaction():
            conn.execute("INSERT INTO events(value) VALUES (?)", ("content",))
            raise RuntimeError("audit failed")

    assert conn.execute("SELECT value FROM events").fetchall() == []


def test_unrelated_thread_cannot_commit_another_threads_partial_transaction():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE events (value TEXT NOT NULL)")
    transaction_started = threading.Event()
    allow_rollback = threading.Event()
    second_attempting = threading.Event()
    second_finished = threading.Event()
    errors = []

    def first_request():
        try:
            with conn.transaction():
                conn.execute("INSERT INTO events(value) VALUES ('request-a')")
                transaction_started.set()
                assert allow_rollback.wait(timeout=3)
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        except BaseException as exc:  # pragma: no cover - diagnostic path
            errors.append(exc)

    def second_request():
        try:
            assert transaction_started.wait(timeout=3)
            second_attempting.set()
            conn.execute("INSERT INTO events(value) VALUES ('request-b')")
            conn.commit()
        except BaseException as exc:  # pragma: no cover - diagnostic path
            errors.append(exc)
        finally:
            second_finished.set()

    a = threading.Thread(target=first_request)
    b = threading.Thread(target=second_request)
    a.start()
    b.start()
    assert transaction_started.wait(timeout=3)
    assert second_attempting.wait(timeout=3)
    # B must block on the transaction lock until A rolls back.
    assert not second_finished.wait(timeout=0.1)
    allow_rollback.set()
    a.join(timeout=3)
    b.join(timeout=3)

    assert not a.is_alive()
    assert not b.is_alive()
    assert errors == []
    values = [row[0] for row in conn.execute("SELECT value FROM events").fetchall()]
    assert values == ["request-b"]


def test_transaction_rejects_implicit_nesting_without_stealing_outer_transaction():
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE events (value TEXT NOT NULL)")

    with conn.transaction():
        conn.execute("INSERT INTO events(value) VALUES ('outer')")
        with pytest.raises(RuntimeError, match="不支持隐式嵌套"):
            with conn.transaction():
                pass
        conn.execute("INSERT INTO events(value) VALUES ('outer-continues')")

    values = [row[0] for row in conn.execute("SELECT value FROM events ORDER BY rowid")]
    assert values == ["outer", "outer-continues"]


def test_database_connect_uses_thirty_second_sqlite_busy_timeout(tmp_path):
    cfg = SimpleNamespace(
        paths=SimpleNamespace(db_file="iterwave.db", backup_dir="backups"),
        resolve_path=lambda value: tmp_path / value,
    )
    conn = Database(cfg).connect()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    finally:
        conn.close()



def test_connection_transaction_supports_raw_sqlite_connection():
    from src.core.db import connection_transaction

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events (value TEXT NOT NULL)")
    with connection_transaction(conn):
        conn.execute("INSERT INTO events(value) VALUES ('committed')")

    with pytest.raises(RuntimeError, match="rollback"):
        with connection_transaction(conn):
            conn.execute("INSERT INTO events(value) VALUES ('rolled-back')")
            raise RuntimeError("rollback")

    assert conn.execute("SELECT value FROM events").fetchall() == [("committed",)]
    conn.close()



def test_abandoned_worker_transaction_is_rolled_back_and_lock_recovered(tmp_path):
    import threading

    conn = LockedConnection(sqlite3.connect(tmp_path / "abandoned.db", check_same_thread=False))
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()
    finished = threading.Event()

    def abandon_after_insert():
        conn.execute("INSERT INTO t VALUES (99)")
        # Deliberately exit with an implicit SQLite transaction still open.
        finished.set()

    worker = threading.Thread(target=abandon_after_insert)
    worker.start()
    worker.join(timeout=2)
    assert finished.is_set()
    assert not worker.is_alive()

    # The next owner detects the dead worker, rolls back its transaction, and proceeds.
    count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert count == 0


def test_transactional_route_respects_own_transaction_marker():
    def endpoint():
        return {"ok": True}

    managed = api_main.manages_own_transaction(endpoint)
    route = api_main.TransactionalAPIRoute("/managed", managed, methods=["POST"])
    assert route.endpoint is not managed
    assert getattr(route.endpoint, "_manages_own_transaction", False)

    plain_route = api_main.TransactionalAPIRoute("/plain", endpoint, methods=["POST"])
    assert plain_route.endpoint is not endpoint



def test_sync_get_endpoint_finalizer_rolls_back_implicit_write(tmp_path):
    raw = sqlite3.connect(tmp_path / "get-write.db", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()
    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx["conn"] = conn

    def legacy_get_route():
        conn.execute("INSERT INTO t VALUES (7)")
        return {"ok": True}

    try:
        route = api_main.TransactionalAPIRoute("/legacy-get", legacy_get_route, methods=["GET"])
        assert route.endpoint() == {"ok": True}
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0

        completed = threading.Event()
        reader = threading.Thread(
            target=lambda: (conn.execute("SELECT COUNT(*) FROM t").fetchone(), completed.set())
        )
        reader.start()
        reader.join(timeout=1)
        assert completed.is_set(), "endpoint finalizer leaked the retained transaction lock"
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)



def test_slow_managed_endpoint_does_not_block_unrelated_read(tmp_path):
    import threading
    import time

    conn = _locked(sqlite3.connect(tmp_path / "slow-managed.db", check_same_thread=False))
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()
    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx["conn"] = conn
    started = threading.Event()
    read_done = threading.Event()

    def slow_external_endpoint():
        started.set()
        time.sleep(0.3)  # Represents remote HTTP/model work before any SQL transaction.
        return {"ok": True}

    managed = api_main.manages_own_transaction(slow_external_endpoint)
    route = api_main.TransactionalAPIRoute("/slow-managed", managed, methods=["POST"])
    worker = threading.Thread(target=route.endpoint)
    try:
        worker.start()
        assert started.wait(timeout=1)
        reader = threading.Thread(
            target=lambda: (conn.execute("SELECT COUNT(*) FROM t").fetchone(), read_done.set())
        )
        reader.start()
        reader.join(timeout=0.15)
        assert read_done.is_set(), "slow non-database work blocked an unrelated tenant read"
        worker.join(timeout=1)
        assert not worker.is_alive()
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)
