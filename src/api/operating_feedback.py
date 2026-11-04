"""APIs for verified publication, independent review tasks and operating feedback."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Query, Request

from src.core import auth

from src.core.operating_feedback import (
    OperatingFeedbackError, acknowledge_close_event, build_decision_context,
    claim_close_events, create_collection_run, create_publish_verification, list_close_events,
    retry_close_event, retry_review_task, save_review_conclusion, utc_now,
)
from src.core.timezone import parse_persisted_datetime


def build_operating_feedback_router(get_runtime) -> APIRouter:
    router = APIRouter()

    def runtime():
        return get_runtime()

    def tenant_id(ctx):
        return str(ctx.get("runtime_tenant_id") or ctx.get("tenant_id") or "default")

    def fail(exc: Exception):
        raise HTTPException(422, str(exc)) from exc

    @router.post("/api/drafts/{draft_id}/publish-verification", status_code=201)
    def verify_publication(draft_id: str, body: dict, request: Request):
        ctx = runtime()
        conn, cfg = ctx["conn"], ctx.get("cfg")
        platform_content_id = str(body.get("platform_content_id") or "").strip()
        content_url = str(body.get("content_url") or body.get("note_url") or "").strip()
        raw_published_at = body.get("published_at")
        published_dt = parse_persisted_datetime(raw_published_at, cfg) if raw_published_at else None
        if published_dt is None:
            raise HTTPException(422, "必须提供真实发布时间（含时区的 ISO 日期时间）")
        from src.core.timezone import storage_now
        if published_dt > storage_now() + timedelta(minutes=5):
            raise HTTPException(422, "真实发布时间不能晚于当前时间")
        scheduled_review_at = body.get("scheduled_review_at")
        if not scheduled_review_at:
            delay_hours = int(getattr(getattr(cfg, "timeouts", None), "metrics_initial_delay_hours", 24) or 24)
            scheduled_review_at = (published_dt + timedelta(hours=max(1, delay_hours))).astimezone(
                __import__("datetime").timezone.utc
            ).isoformat(timespec="seconds")
        if scheduled_review_at:
            scheduled_dt = parse_persisted_datetime(scheduled_review_at, cfg)
            if scheduled_dt is None:
                raise HTTPException(422, "复盘计划时间必须是有效 ISO 日期时间")
            scheduled_review_at = scheduled_dt.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        try:
            with conn:
                result = create_publish_verification(
                    conn, tenant_id=tenant_id(ctx), draft_id=draft_id,
                    platform_content_id=platform_content_id, content_url=content_url,
                    published_at=published_dt.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
                    evidence_source=str(body.get("evidence_source") or "user_confirmed"),
                    evidence_note=str(body.get("evidence_note") or ""),
                    production_line_id=body.get("production_line_id"),
                    scheduled_review_at=scheduled_review_at,
                    max_attempts=int(getattr(getattr(cfg, "timeouts", None), "metrics_retry_attempts", 3) or 3),
                    idempotency_key=body.get("idempotency_key"),
                    actor=auth.audit_actor(request),
                )
            scheduler = ctx.get("scheduler")
            if scheduler and result.get("review_task_id"):
                task_row = conn.execute(
                    "SELECT scheduled_at,status,attempt_count,max_attempts FROM review_tasks WHERE task_id=?",
                    (result["review_task_id"],),
                ).fetchone()
                if task_row and task_row["status"] in {"scheduled", "pending", "running", "partial"} and int(task_row["attempt_count"] or 0) < int(task_row["max_attempts"] or 3):
                    run_at = parse_persisted_datetime(task_row["scheduled_at"], cfg)
                    if run_at is not None:
                        try:
                            scheduler.schedule_review_collection(
                                result["review_task_id"], run_at,
                                attempt=max(1, int(task_row["attempt_count"] or 0) + 1),
                            )
                        except Exception:
                            # The task remains durable in review_tasks; startup recovery re-registers it.
                            import logging
                            logging.getLogger(__name__).exception("独立复盘任务排程失败，等待启动恢复")
            return {"verification": result, "closure_event_pending": result.get("closure_status") != "succeeded"}
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.get("/api/publish-verifications")
    def publish_verifications(limit: int = Query(50, ge=1, le=200)):
        ctx = runtime()
        rows = ctx["conn"].execute(
            """SELECT pv.verification_id,pv.draft_id,pv.content_id,pv.production_task_id,pv.production_line_id,
                      pv.platform,pv.platform_content_id,pv.content_url,pv.published_at,pv.verified_at,
                      pv.content_snapshot_sha256,pv.evidence_source,pv.evidence_note,pv.closure_status,pv.closure_error,pv.created_at,
                      o.event_id AS close_event_id,o.status AS close_event_status
               FROM content_publish_verifications pv
               LEFT JOIN production_line_close_outbox o ON o.verification_id=pv.verification_id
               WHERE pv.tenant_id=? ORDER BY pv.verified_at DESC LIMIT ?""",
            (tenant_id(ctx), limit),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}

    # Task 3 consumes this durable outbox and performs the actual state-machine transition.
    @router.get("/api/production-line-close-events")
    def pending_close_events(limit: int = Query(50, ge=1, le=200)):
        ctx = runtime()
        return {"items": list_close_events(ctx["conn"], tenant_id=tenant_id(ctx), limit=limit)}

    @router.post("/api/production-line-close-events/claim")
    def claim_production_line_close_events(body: dict):
        ctx = runtime()
        try:
            with ctx["conn"]:
                return {"items": claim_close_events(
                    ctx["conn"], tenant_id=tenant_id(ctx),
                    worker_id=str(body.get("worker_id") or ""),
                    limit=int(body.get("limit") or 10),
                    lease_seconds=int(body.get("lease_seconds") or 120),
                )}
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.post("/api/production-line-close-events/{event_id}/ack")
    def acknowledge_production_line_close(event_id: str, body: dict):
        ctx = runtime()
        succeeded = body.get("succeeded")
        if not isinstance(succeeded, bool):
            raise HTTPException(422, "succeeded 必须是布尔值")
        try:
            with ctx["conn"]:
                return acknowledge_close_event(
                    ctx["conn"], event_id, succeeded=succeeded,
                    error=str(body.get("error") or ""), tenant_id=tenant_id(ctx),
                )
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.post("/api/production-line-close-events/{event_id}/retry")
    def retry_production_line_close(event_id: str):
        ctx = runtime()
        event = ctx["conn"].execute(
            "SELECT payload_json FROM production_line_close_outbox WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if event is None or json.loads(event["payload_json"]).get("tenant_id") != tenant_id(ctx):
            raise HTTPException(404, "发布关闭事件不存在")
        try:
            with ctx["conn"]:
                return retry_close_event(ctx["conn"], event_id, tenant_id=tenant_id(ctx))
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.get("/api/review-tasks")
    def review_tasks(status: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
        ctx = runtime()
        params = [tenant_id(ctx)]
        sql = "SELECT * FROM review_tasks WHERE tenant_id=?"
        if status:
            allowed = {"scheduled","pending","running","partial","failed","completed","cancelled"}
            if status not in allowed:
                raise HTTPException(422, "复盘任务状态无效")
            sql += " AND status=?"
            params.append(status)
        rows = ctx["conn"].execute(sql + " ORDER BY scheduled_at DESC LIMIT ?", (*params, limit)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["conclusion"] = json.loads(item.pop("conclusion_json") or "{}")
            items.append(item)
        return {"items": items}

    @router.get("/api/review-tasks/{task_id}")
    def get_review_task(task_id: str):
        ctx = runtime()
        row = ctx["conn"].execute(
            "SELECT * FROM review_tasks WHERE task_id=? AND tenant_id=?",
            (task_id, tenant_id(ctx)),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "复盘任务不存在")
        item = dict(row)
        item["conclusion"] = json.loads(item.pop("conclusion_json") or "{}")
        runs = ctx["conn"].execute(
            "SELECT * FROM review_collection_runs WHERE task_id=? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        item["collection_runs"] = []
        for run in runs:
            run_item = dict(run)
            run_item["metrics"] = json.loads(run_item.pop("metrics_json") or "{}")
            item["collection_runs"].append(run_item)
        return {"task": item}

    @router.post("/api/review-tasks/{task_id}/collect", status_code=201)
    def collect_review_metrics(task_id: str, body: dict, request: Request):
        ctx = runtime()
        conn = ctx["conn"]
        task = conn.execute(
            "SELECT scheduled_at,tenant_id FROM review_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if task is None or task["tenant_id"] != tenant_id(ctx):
            raise HTTPException(404, "复盘任务不存在")
        if task["scheduled_at"] > utc_now() and body.get("force") is not True:
            raise HTTPException(409, "复盘采集尚未到计划时间；人工补采可显式设置 force=true")
        measured = body.get("measured_at")
        measured_dt = parse_persisted_datetime(measured, ctx.get("cfg")) if measured else None
        if measured and measured_dt is None:
            raise HTTPException(422, "采集时间必须是有效 ISO 日期时间")
        window_start = body.get("window_start")
        window_end = body.get("window_end")
        start_dt = parse_persisted_datetime(window_start, ctx.get("cfg")) if window_start else None
        end_dt = parse_persisted_datetime(window_end, ctx.get("cfg")) if window_end else None
        if window_start and start_dt is None or window_end and end_dt is None:
            raise HTTPException(422, "采集窗口必须使用有效 ISO 日期时间")
        if start_dt and end_dt and start_dt > end_dt:
            raise HTTPException(422, "采集窗口结束时间不能早于开始时间")
        timezone_name = str(body.get("timezone") or "UTC")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise HTTPException(422, "时区必须是有效的 IANA 时区名称") from exc
        try:
            with conn:
                result = create_collection_run(
                    conn, task_id=task_id, source=str(body.get("source") or "manual"),
                    status=str(body.get("status") or "succeeded"),
                    metrics=body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
                    window_start=start_dt.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds") if start_dt else None,
                    window_end=end_dt.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds") if end_dt else None,
                    timezone_name=timezone_name,
                    error_message=str(body.get("error") or ""),
                    idempotency_key=body.get("idempotency_key"),
                    measured_at=measured_dt.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds") if measured_dt else None,
                    actor=auth.audit_actor(request),
                )
            return {"run": result}
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.post("/api/review-tasks/{task_id}/retry")
    def retry_review(task_id: str, request: Request, body: dict | None = None):
        ctx = runtime()
        task = ctx["conn"].execute(
            "SELECT tenant_id FROM review_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if task is None or task["tenant_id"] != tenant_id(ctx):
            raise HTTPException(404, "复盘任务不存在")
        requested_at = (body or {}).get("scheduled_at")
        run_at = parse_persisted_datetime(requested_at, ctx.get("cfg")) if requested_at else None
        if requested_at and run_at is None:
            raise HTTPException(422, "重试时间必须是有效 ISO 日期时间")
        due_at = run_at.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="seconds") if run_at else utc_now()
        try:
            with ctx["conn"]:
                task_result = retry_review_task(ctx["conn"], task_id, scheduled_at=due_at, actor=auth.audit_actor(request))
            scheduler = ctx.get("scheduler")
            if scheduler:
                try:
                    scheduler.schedule_review_collection(task_id, run_at or __import__("datetime").datetime.now(__import__("datetime").timezone.utc), attempt=1)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("复盘任务手动重试排程失败，等待启动恢复")
            return {"task": task_result}
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.post("/api/review-tasks/{task_id}/conclusion")
    def review_conclusion(task_id: str, body: dict, request: Request):
        ctx = runtime()
        task = ctx["conn"].execute(
            "SELECT tenant_id FROM review_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if task is None or task["tenant_id"] != tenant_id(ctx):
            raise HTTPException(404, "复盘任务不存在")
        try:
            with ctx["conn"]:
                result = save_review_conclusion(
                    ctx["conn"], task_id=task_id, conclusion=body.get("conclusion", ""),
                    evidence=body.get("evidence", []), target_impact=body.get("target_impact", {}),
                    recommendations=body.get("recommendations", []), gap_changes=body.get("gap_changes", []),
                    actor=auth.audit_actor(request),
                )
            return result
        except OperatingFeedbackError as exc:
            fail(exc)

    @router.get("/api/metric-facts")
    def metric_facts(
        metric_key: str | None = Query(None), content_id: str | None = Query(None),
        limit: int = Query(200, ge=1, le=500),
    ):
        ctx = runtime()
        sql = "SELECT * FROM metric_facts_effective WHERE tenant_id=?"
        params: list = [tenant_id(ctx)]
        if metric_key:
            sql += " AND metric_key=?"
            params.append(metric_key)
        if content_id:
            sql += " AND content_id=?"
            params.append(content_id)
        sql = sql.replace("tenant_id=?", "tenant_id IN (?, 'legacy')", 1)
        rows = ctx["conn"].execute(sql + " ORDER BY measured_at DESC,recorded_at DESC LIMIT ?", (*params, limit)).fetchall()
        return {"items": [dict(row) for row in rows]}

    @router.get("/api/business-gaps")
    def business_gaps(status: str = Query("active", pattern="^(active|resolved|all)$")):
        ctx = runtime()
        sql = "SELECT * FROM business_gaps WHERE tenant_id=?"
        params: list = [tenant_id(ctx)]
        if status != "all":
            sql += " AND status=?"
            params.append(status)
        rows = ctx["conn"].execute(sql + " ORDER BY updated_at DESC", params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            items.append(item)
        return {"items": items}

    @router.get("/api/business-gaps/{gap_id}/history")
    def business_gap_history(gap_id: str):
        ctx = runtime()
        gap = ctx["conn"].execute(
            "SELECT gap_id FROM business_gaps WHERE gap_id=? AND tenant_id=?",
            (gap_id, tenant_id(ctx)),
        ).fetchone()
        if gap is None:
            raise HTTPException(404, "经营缺口不存在")
        rows = ctx["conn"].execute(
            "SELECT * FROM business_gap_events WHERE gap_id=? ORDER BY created_at DESC,event_id DESC",
            (gap_id,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @router.get("/api/contents/{content_id}/visual-regeneration/history")
    def visual_regeneration_history(content_id: str, limit: int = Query(30, ge=1, le=100)):
        ctx = runtime()
        rows = ctx["conn"].execute(
            """SELECT request_id,scope,page_index,original_reason,structured_instruction,
                      preserve_json,forbidden_json,style_baseline_json,reference_images_json,
                      final_prompt_json,source_version_id,output_version_id,visual_job_id,
                      status,error_message,created_at,completed_at
               FROM visual_regeneration_requests
               WHERE content_id=? AND tenant_id=? ORDER BY created_at DESC LIMIT ?""",
            (content_id, tenant_id(ctx), limit),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for source, target in (
                ("preserve_json", "preserve_items"), ("forbidden_json", "forbidden_items"),
                ("style_baseline_json", "style_baseline"), ("reference_images_json", "reference_images"),
                ("final_prompt_json", "final_prompts"),
            ):
                item[target] = json.loads(item.pop(source) or ("[]" if target.endswith("items") or target == "reference_images" else "{}"))
            item["reference_images"] = [Path(str(path)).name for path in item.get("reference_images", [])]
            prompts = item.get("final_prompts")
            if isinstance(prompts, dict) and isinstance(prompts.get("reference_images"), list):
                prompts["reference_images"] = [Path(str(path)).name for path in prompts["reference_images"]]
            items.append(item)
        return {"items": items}

    @router.get("/api/decision-context")
    def latest_decision_context(metric_limit: int = Query(100, ge=1, le=500)):
        ctx = runtime()
        return build_decision_context(ctx["conn"], tenant_id=tenant_id(ctx), metric_limit=metric_limit)

    return router
