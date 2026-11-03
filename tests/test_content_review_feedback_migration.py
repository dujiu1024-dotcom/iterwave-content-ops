from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "133_content_review_feedback_loop.sql"


def _database(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "review-feedback.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE spiral_rounds(id INTEGER PRIMARY KEY);
        CREATE TABLE contents(id TEXT PRIMARY KEY, round_id INTEGER REFERENCES spiral_rounds(id), review_rejected INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE drafts(
            id TEXT PRIMARY KEY,
            content_id TEXT REFERENCES contents(id),
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT '2026-11-03T00:00:00',
            user_confirmed_at TEXT
        );
        CREATE TABLE user_override(
            round_id INTEGER, override_field TEXT, original_value TEXT, overridden_at TEXT
        );
        CREATE TABLE sub_plan_stages(id INTEGER PRIMARY KEY);
        INSERT INTO spiral_rounds(id) VALUES(1);
        INSERT INTO contents(id,round_id,review_rejected) VALUES
            ('published-content',1,0),('draft-content',1,0),('approved-content',1,0),
            ('rejected-content',1,1),('unconfirmed-legacy-content',1,0);
        INSERT INTO drafts(id,content_id,status,user_confirmed_at)
            VALUES('published-draft','published-content','user_published','2026-11-03T09:30:00'),
                  ('ordinary-draft','draft-content','draft_saved',NULL),
                  ('approved-draft','approved-content','draft_saved',NULL),
                  ('rejected-draft','rejected-content','draft_saved',NULL),
                  ('unconfirmed-legacy-draft','unconfirmed-legacy-content','published',NULL);
        INSERT INTO sub_plan_stages(id) VALUES(10);
        """
    )
    conn.execute(
        "INSERT INTO user_override(round_id,override_field,original_value,overridden_at) VALUES(?,?,?,?)",
        (1, "review_approve", '{"content_id":"approved-content"}', "2026-11-03T09:00:00"),
    )
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    return conn


def test_migration_backfills_only_explicit_publish_confirmations(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        published = conn.execute(
            "SELECT publish_status,published_at,publish_time_source FROM drafts WHERE id='published-draft'"
        ).fetchone()
        ordinary = conn.execute(
            "SELECT publish_status,published_at,publish_time_source FROM drafts WHERE id='ordinary-draft'"
        ).fetchone()
        unconfirmed = conn.execute(
            "SELECT publish_status,published_at FROM drafts WHERE id='unconfirmed-legacy-draft'"
        ).fetchone()
        assert tuple(published) == ("published", "2026-11-03T09:30:00", "confirmation_time")
        assert tuple(ordinary) == ("not_published", None, "confirmation_time")
        assert tuple(unconfirmed) == ("not_published", None)
        assert conn.execute("SELECT review_status FROM drafts WHERE id='approved-draft'").fetchone()[0] == "approved"
        assert conn.execute("SELECT review_status FROM drafts WHERE id='rejected-draft'").fetchone()[0] == "rejected"
        assert conn.execute("SELECT review_status FROM drafts WHERE id='ordinary-draft'").fetchone()[0] == "pending"
        conn.execute("UPDATE drafts SET review_status='approved' WHERE id='ordinary-draft'")
        assert conn.execute("SELECT publish_status FROM drafts WHERE id='ordinary-draft'").fetchone()[0] == "not_published"
    finally:
        conn.close()


def test_review_and_strategy_feedback_are_append_only_records(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        conn.execute(
            """INSERT INTO content_review_events
               (content_id,draft_id,round_id,event_type,actor,reason,created_at)
               VALUES('draft-content','ordinary-draft',1,'approved','tester','checked','2026-11-03T10:00:00')"""
        )
        conn.execute(
            """INSERT INTO strategy_effect_reviews
               (stage_id,assessment,recommendation,evidence_note,confidence,evidence_json,created_by,created_at)
               VALUES(10,'mixed','调整选题角度','样本量有限','medium','{}','tester','2026-11-03T10:01:00')"""
        )
        assert conn.execute("SELECT COUNT(*) FROM content_review_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM strategy_effect_reviews").fetchone()[0] == 1
    finally:
        conn.close()


def test_publish_status_rejects_unknown_values(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        try:
            conn.execute("UPDATE drafts SET publish_status='approved' WHERE id='ordinary-draft'")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("publish_status CHECK constraint should reject review states")
    finally:
        conn.close()
