"""Tests for safe orphan-draft inventory.

Legacy orphan rows can predate the current foreign-key guarantees. The fixture
creates those rows with FK checks temporarily disabled to model that state.
"""
from __future__ import annotations

from src.core.draft_recovery import list_orphan_drafts


def _insert_legacy_orphan_draft(conn, draft_id: str, content_id: str, platform: str, status: str) -> None:
    # SQLite only allows changing foreign_keys outside an active transaction.
    # Restore enforcement immediately after seeding the legacy inconsistent row.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(
            "INSERT INTO drafts (id, content_id, platform, status) VALUES (?, ?, ?, ?)",
            (draft_id, content_id, platform, status),
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def test_orphan_drafts_are_reported_without_automatic_cleanup(conn):
    _insert_legacy_orphan_draft(conn, "orphan-pending", "missing-content-1", "xhs", "draft_pending")

    items = list_orphan_drafts(conn)

    assert len(items) == 1
    assert items[0]["id"] == "orphan-pending"
    assert items[0]["recovery_disposition"] == "recovery_candidate"
    assert items[0]["automatic_cleanup_allowed"] is False
    row = conn.execute("SELECT status FROM drafts WHERE id='orphan-pending'").fetchone()
    assert row["status"] == "draft_pending"


def test_orphan_pending_platform_verification_is_never_classified_as_retryable(conn):
    _insert_legacy_orphan_draft(conn, "orphan-uncertain", "missing-content-2", "douyin", "draft_pending")
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id, status) "
        "VALUES ('orphan-uncertain', 'pending_verify')"
    )
    conn.commit()

    items = list_orphan_drafts(conn)

    assert len(items) == 1
    assert items[0]["recovery_disposition"] == "manual_verification_required"
    assert items[0]["automatic_cleanup_allowed"] is False


def test_orphan_saved_draft_preserves_possible_platform_side_effect(conn):
    _insert_legacy_orphan_draft(conn, "orphan-saved", "missing-content-3", "kuaishou", "draft_saved")

    items = list_orphan_drafts(conn)

    assert len(items) == 1
    assert items[0]["recovery_disposition"] == "platform_state_must_be_preserved"
    assert items[0]["automatic_cleanup_allowed"] is False
