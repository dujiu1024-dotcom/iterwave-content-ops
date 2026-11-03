from __future__ import annotations

import sqlite3
from datetime import datetime, timezone as dt_timezone
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


def test_prune_uses_utc_storage_window_and_enforces_retention_and_row_cap(monkeypatch):
    conn = _connection()
    cfg = SimpleNamespace(business_timezone="Asia/Shanghai")
    def fixed_now():
        return datetime(2026, 11, 2, 12, 0, 0, tzinfo=dt_timezone.utc)

    monkeypatch.setattr(timezone, "storage_now", fixed_now)
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

    assert deleted == 2
    assert conn.execute("SELECT id FROM hot_topics").fetchall() == [("recent-b",)]
    conn.close()


def test_prune_rolls_back_all_deletes_when_dangling_link_cleanup_fails(monkeypatch):
    conn = _connection()
    monkeypatch.setattr(
        timezone, "storage_now",
        lambda: datetime(2026, 11, 2, 12, 0, 0, tzinfo=dt_timezone.utc),
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



def _dedup_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE hot_topics (
            id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            platform TEXT,
            title_norm TEXT,
            evidence_status TEXT,
            discovered_at TEXT,
            last_seen_at TEXT,
            seen_count INTEGER DEFAULT 1,
            duplicate_of TEXT
        )
    """)
    return conn


def test_backfill_rolls_back_normalization_when_duplicate_merge_fails():
    from src.core.hot_topic_dedup import backfill

    conn = _dedup_connection()
    conn.executemany(
        "INSERT INTO hot_topics(id,source,title,platform,title_norm,evidence_status,discovered_at,last_seen_at,seen_count) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        [
            ("legacy", "source/xhs", "AI Bot", None, None, "collected",
             "2026-10-01", "2026-11-01", 1),
            ("current", "xhs", "AI Bot", "xhs", "aibot", "collected",
             "2026-10-02", "2026-10-31", 2),
        ],
    )
    conn.execute("""
        CREATE TRIGGER reject_legacy_duplicate_delete
        BEFORE DELETE ON hot_topics
        BEGIN SELECT RAISE(ABORT, 'injected legacy merge failure'); END
    """)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected legacy merge failure"):
        backfill(conn)

    legacy = conn.execute(
        "SELECT platform, title_norm FROM hot_topics WHERE id='legacy'"
    ).fetchone()
    assert tuple(legacy) == (None, None)
    assert conn.execute("SELECT COUNT(*) FROM hot_topics").fetchone()[0] == 2
    conn.close()


def test_cross_platform_dedup_updates_are_transactional():
    from src.core.hot_topic_dedup import mark_cross_platform

    conn = _dedup_connection()
    cfg = SimpleNamespace(business_timezone="Asia/Shanghai")
    now = timezone.storage_now().replace(tzinfo=None).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO hot_topics(id,platform,title_norm,evidence_status,discovered_at,last_seen_at) "
        "VALUES(?,?,?,?,?,?)",
        [
            ("first", "xhs", "aibot", "collected", now, now),
            ("second", "douyin", "aibot", "collected", now, now),
        ],
    )
    conn.execute("""
        CREATE TRIGGER reject_cross_platform_link
        BEFORE UPDATE OF duplicate_of ON hot_topics
        WHEN NEW.duplicate_of IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'injected cross-platform update failure'); END
    """)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected cross-platform update failure"):
        mark_cross_platform(
            conn, threshold=0.8, window_hours=72,
            length_prefilter_ratio=0.5, cfg=cfg,
        )

    assert conn.execute(
        "SELECT duplicate_of FROM hot_topics WHERE id='second'"
    ).fetchone()[0] is None
    conn.close()
