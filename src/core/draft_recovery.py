"""Safe inventory and recovery classification for orphaned draft rows.

An orphan is a draft whose source content row no longer exists. We never delete,
cancel, or mark an orphan as unsaved automatically: a platform side effect may
already have happened before the local content row was lost.
"""
from __future__ import annotations

import json
from typing import Any

from src.core.timezone import storage_now


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
        if verification == "verified_missing" and status == "cancelled":
            disposition = "resolved_platform_missing"
        elif verification == "pending_verify":
            disposition = "manual_verification_required"
        elif status in {"draft_saved", "user_published", "published"}:
            disposition = "platform_state_must_be_preserved"
        elif status in {"cancelled", "failed"}:
            disposition = "review_before_archive"
        else:
            disposition = "recovery_candidate"
        item["recovery_disposition"] = disposition
        item["manual_resolution_allowed"] = (
            status in {"draft_pending", "draft_saved"}
            and verification not in {"verified_saved", "verified_missing"}
        )
        item["automatic_cleanup_allowed"] = False
        result.append(item)
    return result


def resolve_orphan_draft(
    conn: Any, state_machine: Any, draft_id: str, *, saved: bool, note: str, actor: str,
) -> dict[str, Any]:
    """Resolve an orphan only after an explicit human platform check.

    A confirmed-missing orphan is cancelled (never deleted or retried); a
    confirmed-saved orphan is preserved as draft_saved. Published states and
    contradictory prior decisions fail closed.
    """
    row = conn.execute(
        """SELECT d.id,d.status,d.platform,d.content_id,d.draft_id,
                  c.id AS source_content_id,
                  v.status AS verification_status,v.attempted_at,
                  v.platform_draft_id
           FROM drafts d
           LEFT JOIN contents c ON c.id=d.content_id
           LEFT JOIN draft_save_verifications v ON v.draft_id=d.id
           WHERE d.id=?""",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"草稿 {draft_id} 不存在")
    if row["source_content_id"] is not None:
        raise ValueError("该草稿已有来源内容，不属于孤儿草稿")
    if not isinstance(saved, bool) or len(note.strip()) < 3 or len(note) > 500:
        raise ValueError("必须提供 saved 布尔值及 3–500 个字符的核实说明")

    current_status = str(row["status"])
    verification = row["verification_status"]
    target_status = "draft_saved" if saved else "cancelled"
    target_verification = "verified_saved" if saved else "verified_missing"

    if current_status == target_status and verification == target_verification:
        return {
            "draft_id": draft_id, "status": current_status,
            "verification_status": verification, "already_resolved": True,
            "message": "该孤儿草稿已按相同结果完成核实",
        }
    if verification in {"verified_saved", "verified_missing"}:
        raise ValueError("该孤儿草稿已有相反的人工核实结论，禁止覆盖历史决定")
    if current_status not in {"draft_pending", "draft_saved"}:
        raise ValueError(
            f"孤儿草稿当前状态为 {current_status}，不能自动改变已发布或终态记录"
        )

    now = storage_now().isoformat(timespec="seconds")
    if current_status != target_status:
        extra_updates = (
            {
                "draft_id": row["platform_draft_id"] or row["draft_id"],
                "error_msg": None,
            }
            if saved else {
                "draft_id": None,
                "saved_at": None,
                "error_msg": "人工确认平台草稿不存在；来源内容缺失，已归档且禁止自动重试",
            }
        )
        if not state_machine.transition(
            draft_id, target_status,
            reason=f"孤儿草稿人工核实：{'平台已保存' if saved else '平台不存在并归档'}；{note.strip()}",
            extra_updates=extra_updates,
        ):
            raise ValueError("孤儿草稿状态迁移失败，请刷新后重试")

    attempted_at = row["attempted_at"] or now
    platform_draft_id = (
        row["platform_draft_id"] or row["draft_id"] if saved else None
    )
    error_msg = None if saved else "人工确认平台草稿不存在；来源内容缺失，已归档且禁止自动重试"
    with conn:
        if saved and platform_draft_id:
            conn.execute(
                "UPDATE drafts SET draft_id=COALESCE(draft_id,?),saved_at=COALESCE(saved_at,?) "
                "WHERE id=? AND status='draft_saved'",
                (platform_draft_id, now, draft_id),
            )
        conn.execute(
            """INSERT INTO draft_save_verifications(
                   draft_id,status,attempted_at,verified_at,platform_draft_id,error_msg,note
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(draft_id) DO UPDATE SET
                   status=excluded.status,verified_at=excluded.verified_at,
                   platform_draft_id=excluded.platform_draft_id,
                   error_msg=excluded.error_msg,note=excluded.note""",
            (
                draft_id,target_verification,attempted_at,now,
                platform_draft_id,error_msg,note.strip(),
            ),
        )
        conn.execute(
            """INSERT INTO user_override(
                   round_id,override_field,original_value,override_value,
                   reason,overridden_by,overridden_at
               ) VALUES(NULL,?,?,?,?,?,?)""",
            (
                "orphan_draft_resolution",
                json.dumps({
                    "status": current_status,
                    "verification_status": verification,
                }, ensure_ascii=False),
                json.dumps({
                    "status": target_status,
                    "verification_status": target_verification,
                }, ensure_ascii=False),
                note.strip(), actor, now,
            ),
        )
    return {
        "draft_id": draft_id,
        "status": target_status,
        "verification_status": target_verification,
        "already_resolved": False,
        "message": (
            "已确认平台草稿存在，保留本地记录；来源内容仍需单独恢复"
            if saved else "已确认平台草稿不存在，孤儿记录已归档为 cancelled，不会自动重试"
        ),
    }
