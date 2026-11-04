"""API routes for publication verification, review tasks and business feedback."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.core.closed_loop import (
    ClosedLoopError, create_gap_snapshot, create_visual_revision_audit,
    record_review_collection, verify_publication,
)


def build_closed_loop_router(get_runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/publish-verification/candidates")
    def list_publish_verification_candidates(limit: int = Query(100, ge=1, le=500)):
        conn = get_runtime()["conn"]
        rows = conn.execute(
            """SELECT d.id AS draft_id,d.platform,d.platform_note_url,d.user_confirmed_at,
                      c.title,c.id AS content_id
               FROM drafts d JOIN contents c ON c.id=d.content_id
               LEFT JOIN publish_verifications pv ON pv.draft_id=d.id
               WHERE d.user_confirmed_at IS NOT NULL AND pv.id IS NULL
               ORDER BY d.user_confirmed_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return {"items":[dict(row) for row in rows]}

    @router.post("/api/drafts/{draft_id}/publish-verification", status_code=201)
    def verify_draft_publication(draft_id: str, body: dict):
        try:
            row = verify_publication(
                get_runtime()["conn"], draft_id=draft_id,
                platform_content_id=body.get("platform_content_id"),
                platform_url=body.get("platform_url"),
                published_at=body.get("published_at"),
                content_snapshot=body.get("content_snapshot") or {},
                actor="user",
            )
        except ClosedLoopError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"verification": row}

    @router.get("/api/review-tasks")
    def list_review_tasks(status: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
        conn = get_runtime()["conn"]
        if status and status not in {"scheduled","collecting","partial","completed","failed","cancelled"}:
            raise HTTPException(422, "复盘任务状态无效")
        sql = "SELECT * FROM review_tasks"
        args = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        rows = conn.execute(sql + " ORDER BY collect_after DESC,created_at DESC LIMIT ?", (*args,limit)).fetchall()
        return {"items":[dict(row) for row in rows]}

    @router.post("/api/review-tasks/{task_id}/collect")
    def collect_review_task(task_id: str, body: dict):
        try:
            row = record_review_collection(
                get_runtime()["conn"], task_id=task_id,
                result=body.get("result") or {}, status=str(body.get("status") or ""),
                source=str(body.get("source") or "manual"),
                error=body.get("error"), actor="user",
            )
        except ClosedLoopError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"review_task": row}

    @router.get("/api/business-gaps")
    def list_business_gaps(gap_key: str | None = Query(None), limit: int = Query(100, ge=1, le=500)):
        conn = get_runtime()["conn"]
        sql = "SELECT * FROM business_gap_snapshots"
        args = ()
        if gap_key:
            sql += " WHERE gap_key=?"
            args = (gap_key,)
        rows = conn.execute(sql + " ORDER BY observed_at DESC,id DESC LIMIT ?", (*args,limit)).fetchall()
        return {"items":[dict(row) for row in rows]}

    @router.post("/api/business-gaps")
    def add_business_gap(body: dict):
        try:
            row = create_gap_snapshot(
                get_runtime()["conn"], gap_key=body.get("gap_key"),
                status=str(body.get("status") or "open"),
                description=body.get("description"),
                severity=str(body.get("severity") or "medium"),
                evidence=body.get("evidence") or {},
                review_task_id=body.get("review_task_id"), actor="user",
            )
        except ClosedLoopError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"gap": row}

    @router.post("/api/contents/{content_id}/visual-revision-audits", status_code=201)
    def create_visual_revision(content_id: str, body: dict):
        try:
            row = create_visual_revision_audit(
                get_runtime()["conn"], content_id=content_id,
                scope=str(body.get("scope") or ""),
                reason=body.get("reason"),
                normalized_instruction=body.get("normalized_instruction"),
                final_prompt=body.get("final_prompt"),
                style_reference=body.get("style_reference") or {},
                target_page_index=body.get("target_page_index"),
                base_visual_version_id=body.get("base_visual_version_id"),
                preserved_page_hashes=body.get("preserved_page_hashes") or {},
                actor="user",
            )
        except ClosedLoopError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"revision_audit": row}

    @router.get("/api/contents/{content_id}/visual-revision-audits")
    def list_visual_revision_audits(content_id: str, limit: int = Query(50, ge=1, le=200)):
        rows = get_runtime()["conn"].execute(
            "SELECT * FROM visual_revision_audits WHERE content_id=? ORDER BY created_at DESC LIMIT ?",
            (content_id,limit)
        ).fetchall()
        return {"items":[dict(row) for row in rows]}

    @router.get("/api/closed-loop/events")
    def list_pending_closed_loop_events(limit: int = Query(100, ge=1, le=500)):
        rows = get_runtime()["conn"].execute(
            "SELECT id,event_type,aggregate_id,payload_json,status,attempts,created_at "
            "FROM closed_loop_events WHERE status IN ('pending','failed') ORDER BY created_at,id LIMIT ?", (limit,)
        ).fetchall()
        return {"items":[dict(row) for row in rows]}

    @router.patch("/api/closed-loop/events/{event_id}")
    def acknowledge_closed_loop_event(event_id: str, body: dict):
        status = str(body.get("status") or "")
        if status not in {"processing","delivered","failed"}:
            raise HTTPException(422, "事件状态必须为 processing、delivered 或 failed")
        conn = get_runtime()["conn"]
        error = str(body.get("error") or "").strip()[:1000] or None
        cursor = conn.execute(
            """UPDATE closed_loop_events
               SET status=?,attempts=attempts+CASE WHEN ?='processing' THEN 1 ELSE 0 END,
                   last_error=?,delivered_at=CASE WHEN ?='delivered' THEN CURRENT_TIMESTAMP ELSE delivered_at END
               WHERE id=? AND status IN ('pending','processing','failed')""",
            (status,status,error,status,event_id),
        )
        if cursor.rowcount != 1:
            exists = conn.execute("SELECT status FROM closed_loop_events WHERE id=?", (event_id,)).fetchone()
            if exists is None:
                raise HTTPException(404, "闭环事件不存在")
            raise HTTPException(409, "闭环事件已被其他消费者处理或状态不允许变更")
        conn.commit()
        return {"event_id":event_id,"status":status}

    return router
