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
