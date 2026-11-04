"""Tenant-scoped business diagnosis report API."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from src.core.business_diagnosis import _normalize_report, collect_business_context, generate_diagnosis
from src.api.closed_loop import build_closed_loop_router


def build_business_diagnosis_router(get_runtime) -> APIRouter:
    router = APIRouter()

    def _decode(row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["report"] = json.loads(item.pop("report_json"))
            item["source_snapshot"] = json.loads(item.pop("source_snapshot_json"))
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(409, "经营分析报告数据损坏") from exc
        current = collect_business_context(get_runtime()["conn"], get_runtime()["cfg"])
        item["source_is_current"] = item["source_fingerprint"] == current["fingerprint"]
        if item["status"] == "confirmed" and not item["source_is_current"]:
            item["status"] = "stale"
        return item

    def _latest(conn):
        return conn.execute("SELECT * FROM business_diagnosis_reports ORDER BY version_no DESC LIMIT 1").fetchone()

    @router.get("/api/business-diagnosis")
    def latest_report():
        row = _latest(get_runtime()["conn"])
        if row is None:
            return {"report": None, "latest_version": None}
        return {"report": _decode(row)}

    @router.get("/api/business-diagnosis/versions")
    def report_versions():
        rows = get_runtime()["conn"].execute(
            "SELECT id,version_no,status,model_name,created_at,updated_at,confirmed_at,change_note "
            "FROM business_diagnosis_reports ORDER BY version_no DESC LIMIT 100"
        ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @router.post("/api/business-diagnosis/analyze", status_code=201)
    async def analyze():
        runtime = get_runtime()
        conn, cfg = runtime["conn"], runtime["cfg"]
        before = collect_business_context(conn, cfg)
        try:
            report, snapshot, model = await generate_diagnosis(conn, cfg)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        after = collect_business_context(conn, cfg)
        if before["fingerprint"] != after["fingerprint"]:
            raise HTTPException(409, "分析期间业务资料发生变化，请重新分析")
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        row = _latest(conn)
        version = int(row["version_no"]) + 1 if row else 1
        conn.execute("UPDATE business_diagnosis_reports SET status='superseded' WHERE status IN ('draft','confirmed','stale')")
        cur = conn.execute(
            """INSERT INTO business_diagnosis_reports
               (version_no,status,report_json,source_snapshot_json,source_fingerprint,model_name,
                created_at,created_by,updated_at,change_note)
               VALUES(?,'draft',?,?,?,?,?,'ai',?,'AI 重新分析')""",
            (version, json.dumps(report, ensure_ascii=False), json.dumps(before["context"], ensure_ascii=False),
             before["fingerprint"], model, now, now),
        )
        conn.commit()
        return {"report": _decode(conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (cur.lastrowid,)).fetchone())}

    @router.patch("/api/business-diagnosis/{version_id}")
    def update_report(version_id: int, body: dict):
        conn = get_runtime()["conn"]
        row = conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "经营分析报告不存在")
        if row["status"] not in {"draft", "confirmed"}:
            raise HTTPException(409, "只有草稿或当前确认报告可以修改")
        report = body.get("report")
        if not isinstance(report, dict):
            raise HTTPException(422, "report 必须是 JSON 对象")
        current = collect_business_context(conn, get_runtime()["cfg"])
        if row["source_fingerprint"] != current["fingerprint"]:
            conn.execute("UPDATE business_diagnosis_reports SET status='stale' WHERE id=?", (version_id,))
            conn.commit()
            raise HTTPException(409, "报告依据已变化，请重新分析后再修改")
        source_snapshot = json.loads(row["source_snapshot_json"])
        report = _normalize_report(report, source_snapshot.get("source_references", []))
        previous = json.loads(row["report_json"])
        if previous.get("ignored_recommendations"):
            report["ignored_recommendations"] = previous["ignored_recommendations"]
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE business_diagnosis_reports SET report_json=?,status='draft',updated_at=?,change_note=? WHERE id=?",
            (json.dumps(report, ensure_ascii=False), now, str(body.get("change_note") or "用户修改分析报告")[:500], version_id),
        )
        conn.commit()
        return {"report": _decode(conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (version_id,)).fetchone())}

    @router.post("/api/business-diagnosis/{version_id}/ignore")
    def ignore_recommendation(version_id: int, body: dict):
        key = str(body.get("recommendation_key") or "").strip()
        reason = str(body.get("reason") or "").strip()
        if not key or not reason:
            raise HTTPException(422, "必须填写建议标识和忽略原因")
        conn = get_runtime()["conn"]
        row = conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "经营分析报告不存在")
        if row["status"] not in {"draft", "confirmed"}:
            raise HTTPException(409, "只能对当前草稿或确认报告忽略建议")
        current = collect_business_context(conn, get_runtime()["cfg"])
        if row["source_fingerprint"] != current["fingerprint"]:
            conn.execute("UPDATE business_diagnosis_reports SET status='stale' WHERE id=?", (version_id,))
            conn.commit()
            raise HTTPException(409, "业务资料或素材已变化，请重新分析")
        report = json.loads(row["report_json"])
        report.setdefault("ignored_recommendations", []).append({"key": key, "reason": reason})
        conn.execute(
            "UPDATE business_diagnosis_reports SET report_json=?,status='draft',updated_at=?,change_note='用户忽略建议' WHERE id=?",
            (json.dumps(report, ensure_ascii=False), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"), version_id),
        )
        conn.commit()
        return {"report": _decode(conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (version_id,)).fetchone())}

    @router.post("/api/business-diagnosis/{version_id}/confirm")
    def confirm_report(version_id: int):
        conn = get_runtime()["conn"]
        row = conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "经营分析报告不存在")
        if row["status"] != "draft":
            raise HTTPException(409, "只有草稿报告可以确认")
        current = collect_business_context(conn, get_runtime()["cfg"])
        if row["source_fingerprint"] != current["fingerprint"]:
            conn.execute("UPDATE business_diagnosis_reports SET status='stale' WHERE id=?", (version_id,))
            conn.commit()
            raise HTTPException(409, "业务资料或素材已变化，请重新分析")
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            """UPDATE business_diagnosis_reports SET status='confirmed',confirmed_at=?,confirmed_by='user',
               updated_at=? WHERE id=? AND status='draft'""", (now, now, version_id)
        )
        conn.commit()
        return {"report": _decode(conn.execute("SELECT * FROM business_diagnosis_reports WHERE id=?", (version_id,)).fetchone())}

    router.include_router(build_closed_loop_router(get_runtime))
    return router
