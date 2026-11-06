"""Safe inventory and recovery classification for orphaned draft rows.

An orphan is a draft whose source content row no longer exists. We never delete,
cancel, or mark an orphan as unsaved automatically: a platform side effect may
already have happened before the local content row was lost.
"""
from __future__ import annotations

from typing import Any


def list_orphan_drafts(conn: Any) -> list[dict[str, Any]]:
    """Return orphan draft metadata, preserving uncertain platform outcomes."""
    rows = conn.execute(
        """
        SELECT d.id, d.content_id, d.platform, d.status, d.created_at,
               d.saved_at, d.user_confirmed_at, d.error_msg,
               v.status AS verification_status, v.note AS verification_note
        FROM drafts AS d
        LEFT JOIN contents AS c ON c.id = d.content_id
        LEFT JOIN draft_save_verifications AS v ON v.draft_id = d.id
        WHERE c.id IS NULL
        ORDER BY d.created_at ASC, d.id ASC
        """
    ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        verification = item.get("verification_status")
        status = item.get("status")
        if verification == "pending_verify":
            disposition = "manual_verification_required"
        elif status in {"draft_saved", "user_published", "published"}:
            disposition = "platform_state_must_be_preserved"
        elif status in {"cancelled", "failed"}:
            disposition = "review_before_archive"
        else:
            disposition = "recovery_candidate"
        item["recovery_disposition"] = disposition
        item["automatic_cleanup_allowed"] = False
        result.append(item)
    return result
