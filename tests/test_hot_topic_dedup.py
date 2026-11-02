from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.core import timezone
from src.core.hot_topic_dedup import prune


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE hot_topics (
            id TEXT PRIMARY KEY,
            discovered_at TEXT,
            last_seen_at TEXT,
            duplicate_of TEXT
        )
    """)
    return conn


def test_prune_uses_business_timezone_and_enforces_retention_and_row_cap(monkeypatch):
    conn = _connection()
    cfg = SimpleNamespace(business_timezone="Asia/Shanghai")
    observed = {}

    def fixed_now(received_cfg):
        observed["cfg"] = received_cfg
        return datetime(2026, 11, 2, 12, 0, 0)

    monkeypatch.setattr(timezone, "business_now", fixed_now)
    conn.executemany(
        "INSERT INTO hot_topics(id, discovered_at, last_seen_at, duplicate_of) VALUES(?,?,?,?)",
        [
            ("expired", "2026-10-01T00:00:00", "2026-10-01T00:00:00", None),
            ("recent-a", "2026-10-31T00:00:00", "2026-10-31T00:00:00", None),
            ("recent-b", "2026-11-01T00:00:00", "2026-11-01T00:00:00", None),
        ],
    )
    conn.commit()

    deleted = prune(conn, retention_days=7, max_rows=1, cfg=cfg)

    assert observed["cfg"] is cfg
    assert deleted == 2
    assert conn.execute("SELECT id FROM hot_topics").fetchall() == [("recent-b",)]
    conn.close()


def test_prune_rolls_back_all_deletes_when_dangling_link_cleanup_fails(monkeypatch):
    conn = _connection()
    monkeypatch.setattr(
        timezone, "business_now",
        lambda _cfg=None: datetime(2026, 11, 2, 12, 0, 0),
    )
    conn.executemany(
        "INSERT INTO hot_topics(id, discovered_at, last_seen_at, duplicate_of) VALUES(?,?,?,?)",
        [
            ("expired", "2026-10-01T00:00:00", "2026-10-01T00:00:00", None),
            ("recent", "2026-11-01T00:00:00", "2026-11-01T00:00:00", "missing-parent"),
        ],
    )
    conn.execute("""
        CREATE TRIGGER reject_dangling_link_cleanup
        BEFORE UPDATE OF duplicate_of ON hot_topics
        WHEN OLD.duplicate_of IS NOT NULL AND NEW.duplicate_of IS NULL
        BEGIN SELECT RAISE(ABORT, 'injected cleanup failure'); END
    """)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected cleanup failure"):
        prune(conn, retention_days=7, max_rows=100)

    assert conn.execute("SELECT id FROM hot_topics ORDER BY id").fetchall() == [
        ("expired",), ("recent",),
    ]
    assert conn.execute(
        "SELECT duplicate_of FROM hot_topics WHERE id='recent'"
    ).fetchone() == ("missing-parent",)
    conn.close()
