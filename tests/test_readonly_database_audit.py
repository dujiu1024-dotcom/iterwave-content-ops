from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from scripts.audit_database_readonly import audit_auth_store, create_readonly_snapshot


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_readonly_snapshot_does_not_modify_source_database(tmp_path):
    source = tmp_path / "production.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample(value) VALUES ('preserve-me')")
    before = _sha256(source)

    create_readonly_snapshot(source, snapshot)

    assert _sha256(source) == before
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "preserve-me"


def test_historical_auth_audit_reports_aggregates_without_client_addresses(tmp_path):
    auth_db = tmp_path / "auth.sqlite3"
    with sqlite3.connect(auth_db) as conn:
        conn.execute(
            "CREATE TABLE auth_failures (ip TEXT NOT NULL, failed_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO auth_failures(ip, failed_at) VALUES (?, ?)",
            [
                ("203.0.113.17", "2026-11-01T00:00:00"),
                ("203.0.113.17", "2026-11-01T00:01:00"),
                ("2001:db8::5", "2026-11-01T00:02:00"),
            ],
        )
    before = _sha256(auth_db)

    result = audit_auth_store(auth_db)

    assert result == {
        "file_present": True,
        "auth_failures_table_present": True,
        "expected_columns_present": True,
        "record_count": 3,
        "distinct_client_count": 2,
    }
    assert "203.0.113.17" not in repr(result)
    assert "2001:db8::5" not in repr(result)
    assert _sha256(auth_db) == before
