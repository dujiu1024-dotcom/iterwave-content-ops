"""Regression tests for transaction-scoped locking on a shared SQLite connection."""

import sqlite3
import threading

from src.core.db import LockedConnection


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
    from src.core.state_machine import DraftStateMachine, DRAFT_STATUSES

    round_id = seed.round(status="producing")
    saved_id = seed.draft(round_id, draft_status="pending_verify", fetch_status=None)
    retry_id = seed.draft(round_id, draft_status="pending_verify", fetch_status=None)
    machine = DraftStateMachine(conn)

    assert "pending_verify" in DRAFT_STATUSES
    assert machine.transition(
        saved_id, "draft_saved", reason="operator verified platform draft exists",
        extra_updates={"saved_content_hash": "verified-hash", "error_msg": None},
    )
    assert machine.transition(
        retry_id, "draft_pending", reason="operator verified platform draft is absent",
        extra_updates={"draft_id": None, "error_msg": None},
    )
    statuses = {
        row["id"]: row["status"]
        for row in conn.execute(
            "SELECT id, status FROM drafts WHERE id IN (?, ?)", (saved_id, retry_id),
        ).fetchall()
    }
    assert statuses == {saved_id: "draft_saved", retry_id: "draft_pending"}
