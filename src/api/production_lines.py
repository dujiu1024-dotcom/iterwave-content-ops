"""Tenant-scoped APIs for AI-drafted, reviewed production lines."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

from src.core.production_line_store import ProductionLineError, ProductionLineStore

logger = logging.getLogger(__name__)


def _parse_model_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, list):
        raw = "".join(
            part.get("text", "") for part in raw
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(raw, str):
        raise ValueError("模型响应缺少文本")
    start = raw.find("{")
    if start < 0:
        raise ValueError("模型未返回 JSON 对象")
    value, _ = json.JSONDecoder().raw_decode(raw[start:])
    if not isinstance(value, dict):
        raise ValueError("模型返回格式无效")
    return value


def register_production_line_routes(app: FastAPI, get_runtime) -> None:
    def store() -> ProductionLineStore:
        return ProductionLineStore(get_runtime()["conn"])

    @app.get("/api/subplans/{sub_plan_id}/production-line")
    def get_subplan_production_line(sub_plan_id: str):
        conn = get_runtime()["conn"]
        if conn.execute("SELECT 1 FROM sub_plans WHERE id=?", (sub_plan_id,)).fetchone() is None:
            raise HTTPException(404, "子计划不存在")
        return {"production_line": store().get_for_subplan(sub_plan_id)}

    @app.get("/api/production-lines")
    def list_production_lines(status: str | None = Query(None)):
        if status and status not in {"draft", "active", "paused", "completed", "cancelled", "archived"}:
            raise HTTPException(422, "生产线状态无效")
        return {"items": store().list_lines(status=status)}

    @app.post("/api/subplans/{sub_plan_id}/production-line/ai-draft", status_code=201)
    async def create_ai_production_line_draft(sub_plan_id: str, req: dict):
        runtime = get_runtime()
        conn, cfg = runtime["conn"], runtime["cfg"]
        identity = conn.execute(
            """SELECT p.id,p.platform,p.title,p.status,p.current_version_id,
                      v.snapshot_json AS subplan_snapshot,v.version_no AS subplan_version_no,
                      m.plan_snapshot AS master_snapshot,m.status AS master_status
               FROM sub_plans p
               JOIN sub_plan_versions v ON v.id=p.current_version_id AND v.status='effective'
               JOIN master_plan_versions m ON m.id=p.master_plan_version_id
               WHERE p.id=?""",
            (sub_plan_id,),
        ).fetchone()
        if identity is None or identity["status"] != "active" or identity["master_status"] != "effective":
            raise HTTPException(409, "只有母计划和子计划均已生效时才能生成生产线")
        stages = [dict(row) for row in conn.execute(
            """SELECT stage_key,stage_order,title,direction,target_count,strategy_goal,
                      current_problem,execution_strategy,prerequisites,input_info,
                      expected_result,completion_criteria,entry_condition,dependencies_json
               FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order,id""",
            (identity["current_version_id"],),
        ).fetchall()]
        if not stages:
            raise HTTPException(409, "子计划没有策略步骤，无法生成生产线")
        for stage in stages:
            try:
                stage["dependencies"] = json.loads(stage.pop("dependencies_json") or "[]")
            except (TypeError, ValueError):
                raise HTTPException(409, "子计划策略步骤依赖数据损坏")
        try:
            subplan_snapshot = json.loads(identity["subplan_snapshot"])
            master_snapshot = json.loads(identity["master_snapshot"])
        except (TypeError, ValueError):
            raise HTTPException(409, "计划快照损坏，拒绝生成生产线")
        prompt = (
            "你是内容生产线架构师。请把已经审核生效的平台子计划转换成一条可执行的生产线草稿。"
            "只返回 JSON 对象，格式："
            "{production_line:{title,description,execution_mode,stages:["
            "{stage_key,batch_size,production_goal,instructions,cadence}]}}。"
            "必须完整、且仅覆盖输入中的全部 stage_key，每个 key 恰好一次；"
            "不得新增、删除、重命名策略步骤，不得改变策略依赖顺序。"
            "batch_size 是每轮建议生产数量，必须为正整数且不得超过该步骤 target_count。"
            "production_goal 和 instructions 必须落实该步骤的策略目标、当前问题、执行策略、输入信息、预期结果和完成标准。"
            "生产线只负责组织生产，不得替代用户审核发布；不得输出自动发布指令。"
            "\n母计划：" + json.dumps(master_snapshot, ensure_ascii=False)
            + "\n平台子计划：" + json.dumps(subplan_snapshot, ensure_ascii=False)
            + "\n已审核策略步骤：" + json.dumps(stages, ensure_ascii=False)
        )
        providers = [{
            "name": str(getattr(cfg.llm, "provider", "primary") or "primary"),
            "base_url": str(cfg.llm.base_url or "").strip(),
            "model": str(cfg.llm.model or "").strip(),
            "api_key_env": str(cfg.llm.api_key_env or "").strip(),
            "thinking": str(getattr(cfg.llm, "thinking", "") or "").strip(),
        }]
        fallback_model = str(getattr(cfg.llm, "fallback_model", "") or "").strip()
        if fallback_model:
            providers.append({
                "name": str(getattr(cfg.llm, "fallback_provider", "fallback") or "fallback"),
                "base_url": str(getattr(cfg.llm, "fallback_base_url", "") or "").strip(),
                "model": fallback_model,
                "api_key_env": str(getattr(cfg.llm, "fallback_api_key_env", "") or "").strip(),
                "thinking": "",
            })
        attempts, result = [], None
        for provider in providers:
            if not provider["base_url"] or not provider["model"] or not provider["api_key_env"]:
                attempts.append({"provider": provider["name"], "error": "provider_configuration_incomplete"})
                continue
            key = os.getenv(provider["api_key_env"])
            if not key:
                attempts.append({"provider": provider["name"], "error": "api_key_missing"})
                continue
            base = provider["base_url"].rstrip("/")
            url = base if base.endswith("/chat/completions") else base + "/chat/completions"
            body = {"model": provider["model"], "temperature": 0.2,
                    "messages": [{"role": "system", "content": "只输出合法 JSON，不要 Markdown。"},
                                 {"role": "user", "content": prompt}]}
            if provider["thinking"] in {"enabled", "disabled"}:
                body["thinking"] = {"type": provider["thinking"]}
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                    response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=body)
                    response.raise_for_status()
                    payload = response.json()
                    raw = payload["choices"][0]["message"].get("content")
                    parsed = _parse_model_object(raw)
                    result = parsed.get("production_line")
                    if not isinstance(result, dict):
                        raise ValueError("missing_production_line")
                    break
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                attempts.append({"provider": provider["name"], "error": type(exc).__name__})
                logger.warning("AI 生产线生成失败 provider=%s error=%s", provider["name"], type(exc).__name__)
        if not isinstance(result, dict):
            raise HTTPException(502, {"code": "ai_production_line_failed",
                                      "message": "AI 生产线生成失败或返回格式不合法",
                                      "manual_edit_available": True, "attempts": attempts})
        try:
            version = store().create_draft(
                sub_plan_id, int(identity["current_version_id"]), result,
                generation_source="ai", author="ai", change_note="AI 生成生产线草稿，待人工确认",
            )
        except (ProductionLineError, sqlite3.Error) as exc:
            raise HTTPException(422 if isinstance(exc, ProductionLineError) else 503, str(exc)) from exc
        return {"production_line": store().get_for_subplan(sub_plan_id),
                "draft": version, "effective": False, "requires_human_review": True}

    @app.post("/api/subplans/{sub_plan_id}/production-line/draft", status_code=201)
    def create_manual_production_line_draft(sub_plan_id: str, req: dict):
        conn = get_runtime()["conn"]
        identity = conn.execute(
            """SELECT current_version_id FROM sub_plans WHERE id=? AND status='active'""",
            (sub_plan_id,),
        ).fetchone()
        if identity is None or identity["current_version_id"] is None:
            raise HTTPException(409, "子计划尚未审核生效")
        try:
            version = store().create_draft(
                sub_plan_id, int(identity["current_version_id"]), req.get("definition") or {},
                generation_source="manual", author="user",
                change_note=str(req.get("change_note") or "人工编辑生产线草稿"),
                based_on_version_id=req.get("based_on_version_id"),
            )
        except ProductionLineError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"draft": version, "effective": False, "requires_human_review": True}

    @app.post("/api/production-lines/{line_id}/versions/{version_id}/review")
    def review_production_line(line_id: str, version_id: int, req: dict):
        action = str(req.get("action") or "")
        conn = get_runtime()["conn"]
        belongs = conn.execute(
            "SELECT 1 FROM production_line_versions WHERE id=? AND production_line_id=?",
            (version_id, line_id),
        ).fetchone()
        if belongs is None:
            raise HTTPException(404, "生产线版本不存在")
        try:
            if action == "approve":
                version = store().activate(version_id, reviewer="user")
            elif action == "reject":
                version = store().reject(version_id, reviewer="user", reason=str(req.get("reason") or ""))
            else:
                raise HTTPException(422, "action 必须为 approve 或 reject")
        except ProductionLineError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"version": version, "production_line": store().get_for_subplan(version["sub_plan_id"]),
                "effective": action == "approve"}

    @app.patch("/api/subplans/{sub_plan_id}/production-line/status")
    def update_production_line_status(sub_plan_id: str, req: dict):
        try:
            line = store().set_status(
                sub_plan_id, str(req.get("status") or ""),
                actor="user", reason=str(req.get("reason") or ""),
            )
        except ProductionLineError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"production_line": line}
