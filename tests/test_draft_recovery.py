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


def _insert_target_content(conn, content_id: str, platform: str) -> None:
    conn.execute(
        "INSERT INTO contents (id, platform, title, body) VALUES (?, ?, ?, ?)",
        (content_id, platform, "恢复目标", "人工确认后的目标内容"),
    )
    conn.commit()


def test_orphan_recovery_requires_explicit_platform_missing_confirmation(conn):
    from src.core.draft_recovery import recover_orphan_draft

    _insert_legacy_orphan_draft(conn, "orphan-no-confirm", "missing-content-4", "xhs", "draft_pending")
    _insert_target_content(conn, "target-xhs-1", "xhs")

    import pytest
    with pytest.raises(ValueError, match="确认该草稿不存在"):
        recover_orphan_draft(
            conn, draft_id="orphan-no-confirm", target_content_id="target-xhs-1",
            note="已检查", actor="reviewer", platform_missing_confirmed=False,
        )
    row = conn.execute("SELECT content_id FROM drafts WHERE id='orphan-no-confirm'").fetchone()
    assert row["content_id"] == "missing-content-4"


def test_orphan_recovery_relinks_only_after_confirmation_and_writes_audit(conn):
    from src.core.draft_recovery import recover_orphan_draft

    _insert_legacy_orphan_draft(conn, "orphan-recover", "missing-content-5", "douyin", "draft_pending")
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id,status,attempted_at,note) "
        "VALUES ('orphan-recover','pending_verify',CURRENT_TIMESTAMP,'保存结果不确定')"
    )
    _insert_target_content(conn, "target-douyin-1", "douyin")

    result = recover_orphan_draft(
        conn, draft_id="orphan-recover", target_content_id="target-douyin-1",
        note="已登录抖音草稿箱确认该作品不存在", actor="reviewer",
        platform_missing_confirmed=True,
    )

    assert result["source_content_id"] == "missing-content-5"
    assert result["content_id"] == "target-douyin-1"
    row = conn.execute(
        "SELECT content_id FROM drafts WHERE id='orphan-recover'"
    ).fetchone()
    assert row["content_id"] == "target-douyin-1"
    verification = conn.execute(
        "SELECT status,note FROM draft_save_verifications WHERE draft_id='orphan-recover'"
    ).fetchone()
    assert verification["status"] == "verified_missing"
    audit = conn.execute(
        "SELECT source_content_id,target_content_id,actor,note,platform_missing_confirmed "
        "FROM orphan_draft_recovery_audit WHERE draft_id='orphan-recover'"
    ).fetchone()
    assert audit["source_content_id"] == "missing-content-5"
    assert audit["target_content_id"] == "target-douyin-1"
    assert audit["actor"] == "reviewer"
    assert audit["platform_missing_confirmed"] == 1


def test_orphan_recovery_rejects_platform_mismatch_and_existing_platform_draft(conn):
    from src.core.draft_recovery import recover_orphan_draft
    import pytest

    _insert_legacy_orphan_draft(conn, "orphan-mismatch", "missing-content-6", "xhs", "draft_pending")
    _insert_target_content(conn, "target-douyin-2", "douyin")
    with pytest.raises(ValueError, match="平台与孤儿草稿不一致"):
        recover_orphan_draft(
            conn, draft_id="orphan-mismatch", target_content_id="target-douyin-2",
            note="核实平台不存在后重新关联", actor="reviewer",
            platform_missing_confirmed=True,
        )

    _insert_target_content(conn, "target-xhs-2", "xhs")
    conn.execute(
        "INSERT INTO drafts (id,content_id,platform,status) "
        "VALUES ('existing-target-draft','target-xhs-2','xhs','draft_pending')"
    )
    conn.commit()
    with pytest.raises(ValueError, match="已存在同平台草稿"):
        recover_orphan_draft(
            conn, draft_id="orphan-mismatch", target_content_id="target-xhs-2",
            note="核实平台不存在后重新关联", actor="reviewer",
            platform_missing_confirmed=True,
        )


def test_orphan_recovery_never_overwrites_verified_saved_platform_state(conn):
    from src.core.draft_recovery import recover_orphan_draft
    import pytest

    _insert_legacy_orphan_draft(conn, "orphan-platform-saved", "missing-content-7", "kuaishou", "draft_saved")
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id,status,attempted_at,verified_at) "
        "VALUES ('orphan-platform-saved','verified_saved',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    )
    _insert_target_content(conn, "target-kuaishou-1", "kuaishou")
    with pytest.raises(ValueError, match="平台已确认存在"):
        recover_orphan_draft(
            conn, draft_id="orphan-platform-saved", target_content_id="target-kuaishou-1",
            note="重新关联", actor="reviewer", platform_missing_confirmed=True,
        )
