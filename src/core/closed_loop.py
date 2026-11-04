"""Tenant-local persistence for the publication and review feedback loop."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone


class ClosedLoopError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def verify_publication(conn, *, draft_id: str, platform_content_id: str,
                       platform_url: str, published_at: str,
                       content_snapshot: dict, actor: str = "user") -> dict:
    platform_content_id = str(platform_content_id or "").strip()
    platform_url = str(platform_url or "").strip()
    published_at = str(published_at or "").strip()
    if not platform_content_id or not platform_url or not published_at:
        raise ClosedLoopError("平台内容 ID、发布链接和发布时间均为必填项")
    try:
        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClosedLoopError("发布时间必须是有效的 ISO 8601 日期时间") from exc
    # 与当前平台指标策略一致：首次效果采集至少等待 24 小时，避免过早采集零散数据。
    collect_after = (published_dt + timedelta(hours=24)).isoformat(timespec="seconds")
    if not isinstance(content_snapshot, dict) or not content_snapshot:
        raise ClosedLoopError("发布内容快照不能为空")
    draft = conn.execute(
        """SELECT d.id,d.user_confirmed_at,d.platform,c.id AS content_id,c.title,c.body,c.tags,
                  c.round_id,sr.production_line_version_id
           FROM drafts d JOIN contents c ON c.id=d.content_id
           LEFT JOIN spiral_rounds sr ON sr.id=c.round_id
           WHERE d.id=?""", (draft_id,)
    ).fetchone()
    if draft is None:
        raise ClosedLoopError("草稿不存在")
    if not draft["user_confirmed_at"]:
        raise ClosedLoopError("只有用户确认已发布的内容才能进行发布核验")
    existing = conn.execute(
        "SELECT * FROM publish_verifications WHERE draft_id=?", (draft_id,)
    ).fetchone()
    if existing:
        if existing["platform_content_id"] != platform_content_id:
            raise ClosedLoopError("该草稿已绑定其他平台内容 ID，禁止覆盖发布证据")
        return dict(existing)
    now = utc_now()
    verification_id = uuid.uuid4().hex
    snapshot = dict(content_snapshot)
    snapshot.setdefault("title", draft["title"])
    snapshot.setdefault("body", draft["body"])
    snapshot.setdefault("tags", draft["tags"])
    snapshot.setdefault("draft_id", draft_id)
    snapshot.setdefault("round_id", draft["round_id"])
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """INSERT INTO publish_verifications
               (id,draft_id,status,platform_content_id,platform_url,published_at,
                content_snapshot_json,verified_at,verified_by,created_at)
               VALUES(?,?,'verified',?,?,?,?,?,?,?)""",
            (verification_id,draft_id,platform_content_id,platform_url,published_at,
             json.dumps(snapshot,ensure_ascii=False,sort_keys=True),now,actor,now)
        )
        task_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO review_tasks
               (id,draft_id,publish_verification_id,production_line_version_id,status,
                collection_source,collect_after,window_start,platform_content_id,
                publish_snapshot_json,created_at,updated_at)
               VALUES(?,?,?,?,'scheduled','manual',?,?,?, ?,?,?)""",
            (task_id,draft_id,verification_id,draft["production_line_version_id"],
             collect_after,published_at,platform_content_id,
             json.dumps(snapshot,ensure_ascii=False,sort_keys=True),now,now)
        )
        conn.execute(
            """INSERT INTO review_task_events
               (review_task_id,event_type,to_status,detail_json,created_at,actor)
               VALUES(?,'created','scheduled',?,?,?)""",
            (task_id,json.dumps({"verification_id":verification_id},ensure_ascii=False),now,actor)
        )
        # Transactional outbox contract for Task 3: the production-line owner consumes
        # this event and performs its own state-machine transition. This module does not
        # write production_lines.status directly.
        conn.execute(
            """INSERT INTO closed_loop_events
               (id,event_type,aggregate_id,payload_json,status,created_at)
               VALUES(?, 'publication.verified', ?, ?, 'pending', ?)""",
            (uuid.uuid4().hex, str(draft["production_line_version_id"] or draft["round_id"] or draft_id),
             json.dumps({"draft_id":draft_id,"verification_id":verification_id,
                         "production_line_version_id":draft["production_line_version_id"],
                         "review_task_id":task_id},ensure_ascii=False,sort_keys=True),now)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return dict(conn.execute(
        "SELECT * FROM publish_verifications WHERE id=?", (verification_id,)
    ).fetchone())


def record_review_collection(conn, *, task_id: str, result: dict,
                             status: str, source: str = "manual",
                             actor: str = "user", error: str | None = None) -> dict:
    if status not in {"partial","completed","failed"}:
        raise ClosedLoopError("采集状态无效")
    if source not in {"manual","platform_api","platform_readback"}:
        raise ClosedLoopError("采集来源无效")
    if not isinstance(result, dict):
        raise ClosedLoopError("采集结果必须为对象")
    row = conn.execute("SELECT * FROM review_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise ClosedLoopError("复盘任务不存在")
    if row["status"] in {"completed","cancelled"}:
        raise ClosedLoopError("已完成或取消的复盘任务不能再次采集")
    now = utc_now()
    attempts = int(row["attempts"]) + 1
    # Keep missing metrics absent/NULL; zero is only meaningful when explicitly supplied.
    payload = json.dumps(result,ensure_ascii=False,sort_keys=True)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """UPDATE review_tasks SET status=?,collection_source=?,collection_result_json=?,
               attempts=?,last_error=?,updated_at=?,completed_at=?
               WHERE id=? AND status NOT IN ('completed','cancelled')""",
            (status,source,payload,attempts,(str(error)[:1000] if error else None),
             now,now if status=="completed" else None,task_id)
        )
        conn.execute(
            """INSERT INTO review_task_events
               (review_task_id,event_type,from_status,to_status,detail_json,created_at,actor)
               VALUES(?,'collection',?,?,?, ?,?)""",
            (task_id,row["status"],status,
             json.dumps({"source":source,"attempt":attempts,"error":error},ensure_ascii=False),
             now,actor)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return dict(conn.execute("SELECT * FROM review_tasks WHERE id=?", (task_id,)).fetchone())


def create_gap_snapshot(conn, *, gap_key: str, status: str, description: str,
                        severity: str = "medium", evidence: dict | None = None,
                        review_task_id: str | None = None, actor: str = "user") -> dict:
    key, text = str(gap_key or "").strip(), str(description or "").strip()
    if not key or not text:
        raise ClosedLoopError("经营缺口标识和描述不能为空")
    if status not in {"open","shrinking","resolved","new"}:
        raise ClosedLoopError("经营缺口状态无效")
    if severity not in {"low","medium","high","critical"}:
        raise ClosedLoopError("经营缺口严重程度无效")
    now = utc_now()
    cur = conn.execute(
        """INSERT INTO business_gap_snapshots
           (gap_key,status,severity,description,evidence_json,source_review_task_id,
            observed_at,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)""",
        (key,status,severity,text,json.dumps(evidence or {},ensure_ascii=False,sort_keys=True),
         review_task_id,now,now,actor)
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM business_gap_snapshots WHERE id=?", (cur.lastrowid,)).fetchone())


def create_visual_revision_audit(conn, *, content_id: str, scope: str,
                                 reason: str, normalized_instruction: str,
                                 final_prompt: str, style_reference: dict,
                                 target_page_index: int | None = None,
                                 base_visual_version_id: int | None = None,
                                 preserved_page_hashes: dict | None = None,
                                 actor: str = "user") -> dict:
    reason = str(reason or "").strip()
    instruction = str(normalized_instruction or "").strip()
    prompt = str(final_prompt or "").strip()
    if not reason or not instruction or not prompt:
        raise ClosedLoopError("图片重新生成必须填写修改理由，并形成整理指令和最终提示词")
    if scope not in {"whole_set","single_page"}:
        raise ClosedLoopError("重生成范围无效")
    if scope == "single_page" and (target_page_index is None or target_page_index < 0):
        raise ClosedLoopError("单张重生成必须指定有效页码")
    if scope == "whole_set" and target_page_index is not None:
        raise ClosedLoopError("整套重生成不应指定单页")
    if not isinstance(style_reference, dict) or not style_reference:
        raise ClosedLoopError("必须保存整套图片风格参考基准")
    if scope == "single_page":
        required = {"visual_language","primary_colors","secondary_colors","background",
                    "typography","layout","aspect_ratio","whitespace","hierarchy",
                    "rendering_style","neighboring_pages"}
        missing = sorted(required - set(style_reference))
        if missing:
            raise ClosedLoopError("单张重生成缺少风格基准字段: " + ",".join(missing))
    audit_id = uuid.uuid4().hex
    now = utc_now()
    conn.execute(
        """INSERT INTO visual_revision_audits
           (id,content_id,base_visual_version_id,scope,target_page_index,original_reason,
            normalized_instruction,final_prompt,style_reference_json,preserved_page_hashes_json,
            status,created_at,created_by)
           VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (audit_id,content_id,base_visual_version_id,scope,target_page_index,reason,instruction,
         prompt,json.dumps(style_reference,ensure_ascii=False,sort_keys=True),
         json.dumps(preserved_page_hashes or {},ensure_ascii=False,sort_keys=True),now,actor)
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM visual_revision_audits WHERE id=?", (audit_id,)).fetchone())
