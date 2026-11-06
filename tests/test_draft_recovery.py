"""Tests for safe orphan-draft inventory."""
from __future__ import annotations

from src.core.draft_recovery import list_orphan_drafts


def test_orphan_drafts_are_reported_without_automatic_cleanup(conn):
    conn.execute(
        "INSERT INTO drafts (id, content_id, platform, status) "
        "VALUES ('orphan-pending', 'missing-content-1', 'xhs', 'draft_pending')"
    )
    conn.commit()

    items = list_orphan_drafts(conn)

    assert len(items) == 1
    assert items[0]["id"] == "orphan-pending"
    assert items[0]["recovery_disposition"] == "recovery_candidate"
    assert items[0]["automatic_cleanup_allowed"] is False
    row = conn.execute("SELECT status FROM drafts WHERE id='orphan-pending'").fetchone()
    assert row["status"] == "draft_pending"


def test_orphan_pending_platform_verification_is_never_classified_as_retryable(conn):
    conn.execute(
        "INSERT INTO drafts (id, content_id, platform, status) "
        "VALUES ('orphan-uncertain', 'missing-content-2', 'douyin', 'draft_pending')"
    )
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
    conn.execute(
        "INSERT INTO drafts (id, content_id, platform, status) "
        "VALUES ('orphan-saved', 'missing-content-3', 'kuaishou', 'draft_saved')"
    )
    conn.commit()

    items = list_orphan_drafts(conn)

    assert len(items) == 1
    assert items[0]["recovery_disposition"] == "platform_state_must_be_preserved"
    assert items[0]["automatic_cleanup_allowed"] is False
