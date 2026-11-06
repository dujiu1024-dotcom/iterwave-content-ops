"""Tests for safe orphan-draft inventory.

Legacy orphan rows can predate the current foreign-key guarantees. The fixture
creates those rows with FK checks temporarily disabled to model that state.
"""
from __future__ import annotations

import pytest

from src.core.draft_recovery import list_orphan_drafts, resolve_orphan_draft
from src.core.state_machine import DraftStateMachine


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
        "INSERT INTO draft_save_verifications (draft_id, status, attempted_at) "
        "VALUES ('orphan-uncertain', 'pending_verify', CURRENT_TIMESTAMP)"
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



def test_orphan_confirmed_saved_is_preserved_and_audited(conn):
    _insert_legacy_orphan_draft(conn, "orphan-confirmed-saved", "missing-content-4", "xhs", "draft_pending")
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id,status,attempted_at) "
        "VALUES ('orphan-confirmed-saved','pending_verify',CURRENT_TIMESTAMP)"
    )
    conn.commit()

    result = resolve_orphan_draft(
        conn, DraftStateMachine(conn), "orphan-confirmed-saved",
        saved=True, note="已在小红书草稿箱看到对应标题", actor="test-operator",
    )

    assert result["status"] == "draft_saved"
    assert result["verification_status"] == "verified_saved"
    row = conn.execute(
        "SELECT status FROM drafts WHERE id='orphan-confirmed-saved'"
    ).fetchone()
    assert row["status"] == "draft_saved"
    verification = conn.execute(
        "SELECT status,note FROM draft_save_verifications WHERE draft_id='orphan-confirmed-saved'"
    ).fetchone()
    assert verification["status"] == "verified_saved"
    assert "草稿箱" in verification["note"]
    audit = conn.execute(
        "SELECT override_field,overridden_by,round_id FROM user_override "
        "WHERE override_field='orphan_draft_resolution'"
    ).fetchone()
    assert audit["override_field"] == "orphan_draft_resolution"
    assert audit["overridden_by"] == "test-operator"
    assert audit["round_id"] is None
    item = list_orphan_drafts(conn)[0]
    assert item["recovery_disposition"] == "platform_state_must_be_preserved"
    assert item["manual_resolution_allowed"] is False


def test_orphan_confirmed_missing_is_cancelled_not_deleted_or_retried(conn):
    _insert_legacy_orphan_draft(conn, "orphan-confirmed-missing", "missing-content-5", "douyin", "draft_pending")
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id,status,attempted_at) "
        "VALUES ('orphan-confirmed-missing','pending_verify',CURRENT_TIMESTAMP)"
    )
    conn.commit()

    result = resolve_orphan_draft(
        conn, DraftStateMachine(conn), "orphan-confirmed-missing",
        saved=False, note="已检查抖音创作者中心草稿箱，未发现该作品", actor="test-operator",
    )

    assert result["status"] == "cancelled"
    assert result["verification_status"] == "verified_missing"
    row = conn.execute(
        "SELECT status FROM drafts WHERE id='orphan-confirmed-missing'"
    ).fetchone()
    assert row["status"] == "cancelled"
    assert conn.execute(
        "SELECT 1 FROM drafts WHERE id='orphan-confirmed-missing'"
    ).fetchone() is not None
    item = list_orphan_drafts(conn)[0]
    assert item["recovery_disposition"] == "resolved_platform_missing"
    assert item["manual_resolution_allowed"] is False
    assert item["automatic_cleanup_allowed"] is False


def test_orphan_resolution_is_idempotent_and_rejects_conflicting_decision(conn):
    _insert_legacy_orphan_draft(conn, "orphan-idempotent", "missing-content-6", "xhs", "draft_pending")
    conn.commit()
    state_machine = DraftStateMachine(conn)
    first = resolve_orphan_draft(
        conn, state_machine, "orphan-idempotent",
        saved=False, note="确认平台不存在", actor="test-operator",
    )
    repeated = resolve_orphan_draft(
        conn, state_machine, "orphan-idempotent",
        saved=False, note="再次确认平台不存在", actor="test-operator",
    )
    assert first["status"] == "cancelled"
    assert repeated["already_resolved"] is True
    with pytest.raises(ValueError, match="相反的人工核实结论"):
        resolve_orphan_draft(
            conn, state_machine, "orphan-idempotent",
            saved=True, note="重新确认平台存在", actor="test-operator",
        )
