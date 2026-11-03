"""Automatic AI decomposition triggered by master-plan activation."""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import httpx

from src.core.plan_version_store import PlanVersionStore

logger = logging.getLogger(__name__)


def _parse_object(raw: object) -> dict[str, Any]:
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
    if not isinstance(value, dict) or not isinstance(value.get("subplans"), list):
        raise ValueError("模型返回格式无效")
    return value


async def generate_subplan_drafts(conn, cfg, master_version_id: int) -> dict[str, Any]:
    """Create draft-only platform subplans for the just-activated master plan."""
    master = conn.execute(
        "SELECT * FROM master_plan_versions WHERE id=? AND status='effective'",
        (master_version_id,),
    ).fetchone()
    if master is None:
        return {"status": "skipped", "reason": "master_plan_not_effective", "items": []}
    try:
        snapshot = json.loads(master["plan_snapshot"])
    except (TypeError, ValueError):
        return {"status": "failed", "reason": "master_plan_snapshot_invalid", "items": []}
    platforms = list(dict.fromkeys(
        str(p) for p in (snapshot.get("enabled_platforms") or [])
        if p in {"xhs", "douyin", "kuaishou", "wechat_channels"}
    ))
    if not platforms:
        return {"status": "skipped", "reason": "no_enabled_platforms", "items": []}

    # Approval requests may be retried after a network timeout. Never create a
    # second identity for a platform already represented under this master version.
    existing_rows = conn.execute(
        """SELECT p.id,p.platform,p.status,v.id AS version_id,v.status AS version_status
           FROM sub_plans p LEFT JOIN sub_plan_versions v
             ON v.id=p.current_version_id
           WHERE p.master_plan_version_id=? ORDER BY p.platform,p.created_at,p.id""",
        (master_version_id,),
    ).fetchall()
    existing_platforms = {str(row["platform"]) for row in existing_rows}
    existing_items = [{
        "subplan_id": str(row["id"]), "version_id": row["version_id"],
        "platform": str(row["platform"]), "status": str(row["status"]),
        "version_status": row["version_status"],
    } for row in existing_rows]
    platforms_to_generate = [platform for platform in platforms if platform not in existing_platforms]
    if not platforms_to_generate:
        return {"status": "already_generated", "items": existing_items,
                "requires_human_review": True, "effective": False}

    prompt = (
        "你是内容运营计划拆解器。母计划刚刚审核生效，请为每个目标平台生成独立子计划草稿。"
        "只返回 JSON：{subplans:[{platform,title,snapshot,stages:["
        "{stage_key,title,direction,target_count,start_date,end_date,completion_condition,"
        "strategy_goal,current_problem,execution_strategy,prerequisites,input_info,"
        "expected_result,completion_criteria,effect_evaluation,entry_condition,"
        "entry_condition_met,dependencies,notes}]}]}。"
        "必须完整覆盖每个平台，且每个平台仅一项；每个子计划至少一个阶段。"
        "阶段必须按执行顺序排列，stage_key 唯一，dependencies 只能引用同一子计划中更早的阶段，禁止循环依赖。"
        "target_count 表示用户最终确认发布篇数。子计划只能作为待审核草稿，不得自动生效。"
        "\n母计划：" + json.dumps(snapshot, ensure_ascii=False)
        + "\n平台：" + json.dumps(platforms_to_generate, ensure_ascii=False)
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
        body = {"model": provider["model"], "temperature": 0.25,
                "messages": [{"role": "system", "content": "只输出合法 JSON，不要 Markdown。"},
                             {"role": "user", "content": prompt}]}
        if provider["thinking"] in {"enabled", "disabled"}:
            body["thinking"] = {"type": provider["thinking"]}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=body)
                response.raise_for_status()
                payload = response.json()
                result = _parse_object(payload["choices"][0]["message"].get("content"))
                break
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            attempts.append({"provider": provider["name"], "error": type(exc).__name__})
            logger.warning("母计划生效后 AI 子计划拆解失败 provider=%s error=%s",
                           provider["name"], type(exc).__name__)
    if not isinstance(result, dict):
        return {"status": "failed", "reason": "ai_generation_failed", "attempts": attempts, "items": []}

    suggestions = result["subplans"]
    returned = [item.get("platform") for item in suggestions if isinstance(item, dict)]
    if (len(suggestions) != len(platforms_to_generate) or len(returned) != len(platforms_to_generate)
            or set(returned) != set(platforms_to_generate)):
        return {"status": "failed", "reason": "ai_result_incomplete", "items": []}

    created = []
    created_ids = []
    created_versions = []
    store = PlanVersionStore(conn)
    try:
        for item in suggestions:
            platform = str(item["platform"])
            title = str(item.get("title") or "").strip()
            sub_snapshot = item.get("snapshot")
            stages = item.get("stages")
            if not isinstance(sub_snapshot, dict) or not isinstance(stages, list) or not stages:
                raise ValueError("AI 子计划快照或阶段列表无效")
            sub_snapshot.update({"title": title, "platform": platform})
            sub_id = "sub_" + uuid.uuid4().hex
            identity = store.create_subplan(
                sub_id, master_plan_version_id=master_version_id,
                platform=platform, title=title, author="ai",
            )
            created_ids.append(str(identity["id"]))
            version = store.create_subplan_version(
                sub_id, sub_snapshot, master_plan_version_id=master_version_id,
                author="ai", stages=stages, change_note="母计划审核生效后自动 AI 拆解，待人工审核",
            )
            created_versions.append(int(version["id"]))
            created.append({"subplan_id": identity["id"], "version_id": int(version["id"]),
                            "platform": platform, "status": "draft"})
    except Exception as exc:
        # Store methods commit their own transactions. Compensate the whole batch
        # explicitly so a partial AI result cannot leave orphan child identities.
        try:
            for version_id in created_versions:
                conn.execute("DELETE FROM sub_plan_stages WHERE sub_plan_version_id=?", (version_id,))
                conn.execute("DELETE FROM sub_plan_versions WHERE id=? AND status='draft'", (version_id,))
            for subplan_id in created_ids:
                conn.execute("DELETE FROM sub_plans WHERE id=? AND status='draft' AND current_version_id IS NULL",
                             (subplan_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("清理部分创建的 AI 子计划失败")
        logger.exception("母计划自动生成子计划草稿时发生异常")
        return {"status": "failed", "reason": type(exc).__name__, "items": []}
    return {"status": "drafts_created", "items": [*existing_items, *created],
            "requires_human_review": True, "effective": False}
