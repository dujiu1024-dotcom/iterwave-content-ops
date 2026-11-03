import sqlite3
from pathlib import Path

import pytest


def test_business_history_and_plan_snapshot_are_immutable():
    root = Path(__file__).resolve().parents[1] / "migrations"
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE master_plan_versions (
            id INTEGER PRIMARY KEY, status TEXT NOT NULL,
            effective_at TEXT, reviewed_at TEXT, created_at TEXT NOT NULL
        );
    """)
    conn.executescript((root / "143_business_analysis_foundation.sql").read_text())
    conn.executescript((root / "146_immutable_business_snapshots.sql").read_text())
    conn.execute(
        "INSERT INTO business_profile_versions(version_no,snapshot_json,created_at) "
        "VALUES (1,'{}','2026-11-04')"
    )
    conn.execute(
        "INSERT INTO master_plan_versions(id,status,created_at) VALUES (1,'draft','2026-11-04')"
    )
    conn.execute("UPDATE master_plan_versions SET status='effective' WHERE id=1")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE business_profile_versions SET snapshot_json='x' WHERE version_no=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM business_profile_versions WHERE version_no=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE master_plan_business_snapshots SET snapshot_json='x' WHERE master_plan_version_id=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM master_plan_business_snapshots WHERE master_plan_version_id=1")
    conn.close()
