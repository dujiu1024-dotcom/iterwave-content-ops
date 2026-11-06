"""Safe inventory and explicitly confirmed recovery for orphaned draft rows.

An orphan is a draft whose source content row no longer exists. No platform
side effect is ever retried, deleted, or reversed by this module.
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
        item["manual_recovery_allowed"] = (
            status in {"draft_pending", "draft_saved", "failed", "cancelled", "expired"}
            and verification != "verified_saved"
        )
        item["manual_platform_confirmation_allowed"] = (
            status in {"draft_pending", "draft_saved"}
            and verification not in {"verified_saved", "verified_missing"}
        )
        result.append(item)
    return result


def recover_orphan_draft(
    conn: Any,
    *,
    draft_id: str,
    target_content_id: str,
    note: str,
    actor: str,
    platform_missing_confirmed: bool,
) -> dict[str, Any]:
    """Relink an orphan only after an explicit human confirmation of platform absence.

    Caller must hold an IMMEDIATE transaction. The original content ID and
    operator attestation are retained in an append-only audit table.
    """
    clean_note = str(note or "").strip()
    if platform_missing_confirmed is not True:
        raise ValueError("必须先在对应平台确认该草稿不存在")
    if len(clean_note) < 5 or len(clean_note) > 500:
        raise ValueError("恢复说明长度必须为 5–500 个字符")
    if not str(actor or "").strip():
        raise ValueError("缺少操作人身份")

    row = conn.execute(
        """SELECT d.id,d.content_id,d.platform,d.status,
                  v.status AS verification_status
           FROM drafts d
           LEFT JOIN contents c ON c.id=d.content_id
           LEFT JOIN draft_save_verifications v ON v.draft_id=d.id
           WHERE d.id=? AND c.id IS NULL""",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise LookupError("孤儿草稿不存在，或已被其他操作恢复")
    if row["status"] not in {"draft_pending", "draft_saved", "failed", "cancelled", "expired"}:
        raise ValueError("当前草稿状态不允许孤儿恢复；已发布或正在采集的草稿必须保留")
    if row["verification_status"] == "verified_saved":
        raise ValueError("平台已确认存在该草稿，必须保留平台状态")
    target = conn.execute(
        "SELECT id,platform FROM contents WHERE id=?",
        (target_content_id,),
    ).fetchone()
    if target is None:
        raise LookupError("目标内容不存在")
    if target["platform"] != row["platform"]:
        raise ValueError("目标内容的平台与孤儿草稿不一致")
    conflict = conn.execute(
        "SELECT id FROM drafts WHERE content_id=? AND platform=? AND id<>? LIMIT 1",
        (target_content_id, row["platform"], draft_id),
    ).fetchone()
    if conflict:
        raise ValueError("目标内容已存在同平台草稿，不能重复关联")

    conn.execute(
        "UPDATE drafts SET content_id=? WHERE id=? AND content_id=?",
        (target_content_id, draft_id, row["content_id"]),
    )
    if row["verification_status"] == "pending_verify":
        conn.execute(
            """UPDATE draft_save_verifications
               SET status='verified_missing',verified_at=CURRENT_TIMESTAMP,
                   note=COALESCE(note || char(10),'') || ?
               WHERE draft_id=? AND status='pending_verify'""",
            ("人工确认平台不存在后恢复关联：" + clean_note, draft_id),
        )
    elif row["verification_status"] is None:
        conn.execute(
            """INSERT INTO draft_save_verifications(
                   draft_id,status,attempted_at,verified_at,note
               ) VALUES(?,'verified_missing',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?)""",
            (draft_id, "人工确认平台不存在后恢复关联：" + clean_note),
        )
    else:
        conn.execute(
            """UPDATE draft_save_verifications
               SET status='verified_missing',verified_at=CURRENT_TIMESTAMP,
                   note=COALESCE(note || char(10),'') || ?
               WHERE draft_id=?""",
            ("人工再次确认平台不存在后恢复关联：" + clean_note, draft_id),
        )

    conn.execute(
        """INSERT INTO orphan_draft_recovery_audit(
               draft_id,source_content_id,target_content_id,platform,
               previous_status,previous_verification_status,actor,note,
               platform_missing_confirmed
           ) VALUES(?,?,?,?,?,?,?,?,1)""",
        (
            draft_id, row["content_id"], target_content_id, row["platform"],
            row["status"], row["verification_status"], actor, clean_note,
        ),
    )
    return {
        "draft_id": draft_id,
        "source_content_id": row["content_id"],
        "content_id": target_content_id,
        "platform": row["platform"],
        "status": row["status"],
        "verification_status": "verified_missing",
    }

 
def confirm_orphan_platform_saved(
    conn: Any, *, draft_id: str, note: str, actor: str,
) -> dict[str, Any]:
    """Preserve an orphan whose platform-side draft was explicitly found.

    This records the platform fact without fabricating or relinking missing
    source content. Caller must hold an IMMEDIATE transaction.
    """
    clean_note = str(note or "").strip()
    if len(clean_note) < 5 or len(clean_note) > 500:
        raise ValueError("核实说明长度必须为 5–500 个字符")
    if not str(actor or "").strip():
        raise ValueError("缺少操作人身份")
    row = conn.execute(
        """SELECT d.id,d.content_id,d.platform,d.status,d.draft_id,
                  c.id AS source_content_id,v.status AS verification_status,
                  v.attempted_at,v.platform_draft_id
           FROM drafts d
           LEFT JOIN contents c ON c.id=d.content_id
           LEFT JOIN draft_save_verifications v ON v.draft_id=d.id
           WHERE d.id=? AND c.id IS NULL""",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise LookupError("孤儿草稿不存在，或已被其他操作恢复")
    if row["status"] not in {"draft_pending", "draft_saved"}:
        raise ValueError("当前状态不允许确认平台草稿存在；已发布或终态记录必须保留")
    if row["verification_status"] == "verified_missing":
        raise ValueError("该孤儿草稿已有平台不存在的核实结论，禁止覆盖")
    if row["verification_status"] == "verified_saved":
        return {"draft_id": draft_id, "status": row["status"],
                "verification_status": "verified_saved", "already_confirmed": True}
    from src.core.timezone import storage_now
    now = storage_now().isoformat(timespec="seconds")
    platform_draft_id = row["platform_draft_id"] or row["draft_id"]
    conn.execute(
        """UPDATE drafts SET status='draft_saved',
               saved_at=COALESCE(saved_at,?), error_msg=NULL
           WHERE id=? AND status IN ('draft_pending','draft_saved')""",
        (now, draft_id),
    )
    conn.execute(
        """INSERT INTO draft_save_verifications(
               draft_id,status,attempted_at,verified_at,platform_draft_id,note
           ) VALUES(?,'verified_saved',COALESCE(?,?),?,?,?)
           ON CONFLICT(draft_id) DO UPDATE SET
               status='verified_saved',verified_at=excluded.verified_at,
               platform_draft_id=COALESCE(excluded.platform_draft_id,draft_save_verifications.platform_draft_id),
               error_msg=NULL,note=excluded.note""",
        (draft_id,row["attempted_at"],now,now,platform_draft_id,clean_note),
    )
    conn.execute(
        """INSERT INTO orphan_draft_platform_confirmation_audit(
               draft_id,source_content_id,platform,previous_status,
               previous_verification_status,actor,note,platform_saved_confirmed
           ) VALUES(?,?,?,?,?,?,?,1)""",
        (draft_id,row["content_id"],row["platform"],row["status"],
         row["verification_status"],actor,clean_note),
    )
    return {
        "draft_id": draft_id, "status": "draft_saved",
        "verification_status": "verified_saved",
        "source_content_missing": True,
        "message": "已确认平台草稿存在并保留平台状态；来源内容仍缺失，需单独恢复，不会伪造正文或素材",
    }
