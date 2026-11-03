"""Tenant-scoped business profile, catalog and plan snapshot APIs."""
from __future__ import annotations

import json
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException, Query

logger = logging.getLogger(__name__)


def register_business_profile_routes(app: FastAPI, get_runtime) -> None:
    def conn():
        return get_runtime()["conn"]

    @app.get("/api/business-profile")
    def read_profile():
        from src.core.business_profile_repository import get_profile
        return get_profile(conn())

    @app.put("/api/business-profile")
    def update_profile(payload: dict):
        from src.core.business_profile_repository import save_profile
        try:
            return save_profile(conn(), payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/business-profile/items")
    def read_items(entity_type: str | None = Query(None)):
        from src.core.business_profile_repository import list_items
        try:
            return {"items": list_items(conn(), entity_type)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/business-profile/items/{entity_type}")
    def create_item(entity_type: str, payload: dict):
        from src.core.business_profile_repository import save_item
        try:
            return save_item(conn(), entity_type, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/business-profile/items/{entity_type}/{item_id}")
    def update_item(entity_type: str, item_id: str, payload: dict):
        from src.core.business_profile_repository import save_item
        try:
            return save_item(conn(), entity_type, payload, item_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/business-profile/items/{item_id}")
    def delete_item(item_id: str):
        from src.core.business_profile_repository import archive_item
        if not archive_item(conn(), item_id):
            raise HTTPException(404, "业务资料不存在")
        return {"id": item_id, "archived": True}

    @app.get("/api/business-profile/versions")
    def read_versions(limit: int = Query(50, ge=1, le=200)):
        from src.core.business_profile_repository import list_versions
        return {"items": list_versions(conn(), limit)}

    @app.get("/api/business-profile/versions/{version_no}")
    def read_business_version(version_no: int):
        from src.core.business_profile_repository import get_version
        row = get_version(conn(), version_no)
        if row is None:
            raise HTTPException(404, "业务资料版本不存在")
        return row

    @app.get("/api/master-plans/{version_id}/business-snapshot")
    def read_plan_business_snapshot(version_id: int):
        from src.core.business_profile_repository import plan_snapshot
        row = plan_snapshot(conn(), version_id)
        if row is None:
            raise HTTPException(404, "该计划没有可用的业务资料快照")
        return row

    @app.get("/api/business-profile/analysis")
    def analyze_profile():
        from src.core.business_profile_repository import get_snapshot
        snapshot = get_snapshot(conn())
        missing = []
        profile = snapshot["profile"]
        required = {
            "business_name": "业务名称", "introduction": "业务介绍",
            "industry": "所属行业", "business_model": "商业模式",
            "operating_goal": "经营目标",
        }
        for field, label in required.items():
            if not str(profile.get(field) or "").strip():
                missing.append({"field": field, "label": label, "status": "missing"})
        types = {item["entity_type"] for item in snapshot["items"]}
        if not types.intersection({"product", "service"}):
            missing.append({"field": "offerings", "label": "至少一项产品或服务", "status": "missing"})
        if "audience" not in types:
            missing.append({"field": "audience", "label": "目标客户资料", "status": "missing"})
        total_checks = len(required) + 2
        return {
            "missing": missing,
            "completeness": round((total_checks - len(missing)) / total_checks * 100),
            "snapshot": snapshot,
            "note": "此接口只检查资料完整性，不会把推测内容写入业务档案。AI 整理建议需另行提交并由用户确认。",
        }

    # AI returns an uncommitted proposal. Canonical data changes only after explicit confirmation.
    @app.post("/api/business-profile/ai-proposals")
    async def create_ai_proposal(payload: dict):
        source_text = str(payload.get("source_text") or "").strip()
        if not source_text or len(source_text) > 20000:
            raise HTTPException(422, "请提供 1-20000 个字符的原始资料")
        cfg = get_runtime()["cfg"]
        providers = [{
            "name": str(getattr(cfg.llm, "provider", "primary") or "primary"),
            "base_url": str(cfg.llm.base_url or "").strip(),
            "model": str(cfg.llm.model or "").strip(),
            "api_key_env": str(cfg.llm.api_key_env or "").strip(),
        }]
        fallback_model = str(getattr(cfg.llm, "fallback_model", "") or "").strip()
        if fallback_model:
            providers.append({
                "name": str(getattr(cfg.llm, "fallback_provider", "fallback") or "fallback"),
                "base_url": str(getattr(cfg.llm, "fallback_base_url", "") or "").strip(),
                "model": fallback_model,
                "api_key_env": str(getattr(cfg.llm, "fallback_api_key_env", "") or "").strip(),
            })
        prompt = (
            "将用户提供的经营资料整理为 JSON，不得补造事实。"
            "格式：{proposals:[{entity_type:'profile|product|service|audience|market',"
            "name:string,details:object,field_evidence:object,questions:string[]}]}。"
            "每个提取字段的 field_evidence.status 必须为 ai_inferred，source 写明来自用户原文；"
            "不确定或缺失的信息放入 questions，不要猜测，只能输出原文有依据的字段。"
            "profile 的 name 固定为业务概况，details 仅使用 business_name,introduction,industry,business_model,business_stage,operating_goal。"
            "product 的 details 可使用 product_type,introduction,selling_points,price_range,target_audience,use_cases,differentiators,faqs,purchase_reasons。"
            "service 的 details 可使用 service_type,introduction,delivery_method,price_range,target_audience,use_cases,differentiators,faqs,purchase_reasons。"
            "audience 的 details 可使用 customer_type,age_range,regions,needs,pain_points,decision_factors。"
            "market 的 details 可使用 competitors,competing_products,positioning,differentiation,opportunities。"
            "产品卖点、客户痛点、购买理由和差异化优势必须可追溯到原始资料；没有证据就提出问题。"
            "\\n用户原始资料：\\n" + source_text
        )
        attempts = []
        for provider in providers:
            key = os.getenv(provider["api_key_env"]) if provider["api_key_env"] else None
            if not provider["base_url"] or not provider["model"] or not key:
                attempts.append({"provider": provider["name"], "error": "provider_configuration_incomplete"})
                continue
            base = provider["base_url"].rstrip("/")
            url = base if base.endswith("/chat/completions") else base + "/chat/completions"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                    response = await client.post(
                        url, headers={"Authorization": f"Bearer {key}"},
                        json={"model": provider["model"], "temperature": 0.1,
                              "messages":[{"role":"system","content":"只返回合法 JSON，不要 Markdown。"},
                                          {"role":"user","content":prompt}]},
                    )
                    response.raise_for_status()
                    raw = response.json()["choices"][0]["message"]["content"]
                    if isinstance(raw, list):
                        raw = "".join(part.get("text","") for part in raw if isinstance(part,dict))
                    start = raw.find("{")
                    result, _ = json.JSONDecoder().raw_decode(raw[start:])
                    proposals = result.get("proposals")
                    if not isinstance(proposals, list):
                        raise ValueError("proposals_missing")
                    for proposal in proposals:
                        if not isinstance(proposal, dict) or proposal.get("entity_type") not in {"profile","product","service","audience","market"}:
                            raise ValueError("invalid_proposal")
                        evidence = proposal.get("field_evidence") or {}
                        for field in evidence.values():
                            if not isinstance(field, dict) or field.get("status") != "ai_inferred":
                                raise ValueError("invalid_evidence")
                    return {"status":"needs_confirmation","proposals":proposals,
                            "message":"AI 已生成待确认建议；尚未写入正式业务档案。"}
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                attempts.append({"provider":provider["name"],"error":type(exc).__name__})
                logger.warning("业务资料 AI 整理失败 provider=%s error=%s",provider["name"],type(exc).__name__)
        return {"status":"needs_model_provider","proposals":[],"attempts":attempts,
                "message":"没有可用的模型提供方；原始资料未写入正式档案。"}

    @app.post("/api/business-profile/ai-proposals/confirm")
    def confirm_ai_proposal(payload: dict):
        from src.core.business_profile_repository import save_profile, save_item
        proposal = payload.get("proposal")
        if not isinstance(proposal, dict):
            raise HTTPException(422, "缺少待确认建议")
        entity_type = proposal.get("entity_type")
        details = proposal.get("details") or {}
        evidence = proposal.get("field_evidence") or {}
        if not isinstance(details, dict) or not isinstance(evidence, dict):
            raise HTTPException(422, "建议内容结构无效")
        confirmed_evidence = {}
        for field in details:
            item = evidence.get(field)
            if not isinstance(item, dict) or item.get("status") != "ai_inferred":
                raise HTTPException(422, "AI 建议必须保留 ai_inferred 来源标记")
            confirmed_evidence[field] = {"status":"user_confirmed","source":item.get("source"),"confirmed_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
        try:
            if entity_type == "profile":
                allowed = {"business_name","introduction","industry","business_model","business_stage","operating_goal"}
                if set(details) - allowed:
                    raise ValueError("业务概况包含不支持的字段")
                return save_profile(conn(), {**details,"field_evidence":confirmed_evidence,"change_note":"用户确认 AI 整理建议"})
            if entity_type in {"product","service","audience","market"}:
                return save_item(conn(), entity_type, {
                    "name": proposal.get("name"),"details":details,"field_evidence":confirmed_evidence,
                })
            raise ValueError("不支持的建议类型")
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
