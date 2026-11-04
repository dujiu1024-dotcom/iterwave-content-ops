"""Generate evidence-bound, draft-only master plans from confirmed diagnosis reports."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.plan_version import PlanVersionManager

logger = logging.getLogger(__name__)
VALID_PLATFORMS = {"xhs", "douyin", "kuaishou", "wechat_channels"}


class MasterPlanGenerationError(ValueError):
    """The model output cannot be represented by the current master-plan schema."""


def parse_generation_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        raw = "".join(x.get("text", "") for x in raw if isinstance(x, dict) and isinstance(x.get("text"), str))
    if not isinstance(raw, str):
        raise MasterPlanGenerationError("模型未返回文本")
    start = raw.find("{")
    if start < 0:
        raise MasterPlanGenerationError("模型未返回 JSON 对象")
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except ValueError as exc:
        raise MasterPlanGenerationError("模型返回的 JSON 无法解析") from exc
    if not isinstance(value, dict) or not isinstance(value.get("plan"), dict):
        raise MasterPlanGenerationError("模型结果必须包含 plan 对象")
    return value


def validate_master_plan(plan: dict[str, Any], cfg) -> dict[str, Any]:
    """Validate strictly, then normalize through the existing master-plan builder."""
    allowed = {
        "topic_count_per_platform", "dimension_allocation", "enabled_platforms",
        "content_format", "per_platform", "cadence",
    }
    if set(plan) - allowed:
        raise MasterPlanGenerationError("plan 包含当前母计划 schema 不支持的字段")
    count = plan.get("topic_count_per_platform")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 50:
        raise MasterPlanGenerationError("topic_count_per_platform 必须为 1-50 的整数")
    platforms = plan.get("enabled_platforms")
    if not isinstance(platforms, list) or not platforms or any(p not in VALID_PLATFORMS for p in platforms):
        raise MasterPlanGenerationError("enabled_platforms 必须至少包含一个有效平台")
    if len(set(platforms)) != len(platforms):
        raise MasterPlanGenerationError("enabled_platforms 不能重复")
    allocation = plan.get("dimension_allocation", {})
    if not isinstance(allocation, dict):
        raise MasterPlanGenerationError("dimension_allocation 必须是对象")
    total = 0
    for name, amount in allocation.items():
        if not str(name).strip() or isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise MasterPlanGenerationError("维度分配必须使用非负整数和非空维度名")
        total += amount
    if total > count:
        raise MasterPlanGenerationError("维度分配合计不能超过每个平台选题数")
    content_format = plan.get("content_format", "image_text")
    if content_format != "image_text":
        raise MasterPlanGenerationError("当前系统仅支持图文内容计划")
    per_platform = plan.get("per_platform", {})
    if not isinstance(per_platform, dict):
        raise MasterPlanGenerationError("per_platform 必须是对象")
    for platform, conf in per_platform.items():
        if platform not in platforms or not isinstance(conf, dict):
            raise MasterPlanGenerationError("per_platform 只能配置已启用的平台")
        topic_count = conf.get("topic_count")
        if topic_count is not None and (
            isinstance(topic_count, bool) or not isinstance(topic_count, int)
            or not 1 <= topic_count <= count
        ):
            raise MasterPlanGenerationError(f"{platform} 的 topic_count 必须在 1-{count} 之间")
        slots = conf.get("publish_slots", [])
        if not isinstance(slots, list) or any(
            not isinstance(slot, str) or not re.fullmatch(r"(?:[01]\\d|2[0-3]):[0-5]\\d", slot)
            for slot in slots
        ):
            raise MasterPlanGenerationError(f"{platform} 的 publish_slots 必须为 HH:MM 数组")
    cadence = plan.get("cadence", {})
    if not isinstance(cadence, dict):
        raise MasterPlanGenerationError("cadence 必须是对象")
    if cadence.get("round_interval_days") is not None:
        value = cadence["round_interval_days"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.5 <= value <= 90:
            raise MasterPlanGenerationError("round_interval_days 必须在 0.5-90 之间")
    if cadence.get("rounds_planned") is not None:
        value = cadence["rounds_planned"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
            raise MasterPlanGenerationError("rounds_planned 必须为 1-200 的整数")
    if cadence.get("goal_id") is not None and not isinstance(cadence["goal_id"], (str, int)):
        raise MasterPlanGenerationError("cadence.goal_id 格式无效")
    # Reuse the canonical builder so no model-controlled extension fields reach storage.
    return PlanVersionManager(cfg, None).build_plan(
        topic_count_per_platform=count,
        platforms=platforms,
        dimension_allocation=allocation,
        content_format=content_format,
        per_platform=per_platform,
        cadence=cadence,
    )


async def _call_model(cfg, prompt: str) -> tuple[dict[str, Any], str]:
    providers = [{
        "name": str(getattr(cfg.llm, "provider", "primary") or "primary"),
        "base_url": str(cfg.llm.base_url or "").strip(),
        "model": str(cfg.llm.model or "").strip(),
        "api_key_env": str(cfg.llm.api_key_env or "").strip(),
        "thinking": str(getattr(cfg.llm, "thinking", "") or "").strip(),
    }]
    fallback = str(getattr(cfg.llm, "fallback_model", "") or "").strip()
    if fallback:
        providers.append({
            "name": str(getattr(cfg.llm, "fallback_provider", "fallback") or "fallback"),
            "base_url": str(getattr(cfg.llm, "fallback_base_url", "") or "").strip(),
            "model": fallback,
            "api_key_env": str(getattr(cfg.llm, "fallback_api_key_env", "") or "").strip(),
            "thinking": "",
        })
    errors = []
    for provider in providers:
        key = os.getenv(provider["api_key_env"]) if provider["api_key_env"] else None
        if not provider["base_url"] or not provider["model"] or not key:
            errors.append(provider["name"] + ":configuration_incomplete")
            continue
        url = provider["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        body = {
            "model": provider["model"], "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "只输出合法 JSON，不要 Markdown。"},
                {"role": "user", "content": prompt},
            ],
        }
        if provider["thinking"] in {"enabled", "disabled"}:
            body["thinking"] = {"type": provider["thinking"]}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=body)
                response.raise_for_status()
                payload = response.json()
                parsed = parse_generation_response(payload["choices"][0]["message"].get("content"))
                return parsed, provider["model"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            errors.append(provider["name"] + ":" + type(exc).__name__)
            logger.warning("AI 母计划生成失败 provider=%s error=%s", provider["name"], type(exc).__name__)
    raise RuntimeError("AI 母计划生成失败：" + ", ".join(errors))


async def generate_master_plan_draft(conn, cfg) -> dict[str, Any]:
    """Gate on a current confirmed diagnosis, generate, re-check, and persist draft only."""
    from src.core.business_diagnosis import collect_business_context
    from src.core.plan_version_store import PlanVersionStore, PlanVersionError

    diagnosis = conn.execute(
        "SELECT * FROM business_diagnosis_reports ORDER BY version_no DESC LIMIT 1"
    ).fetchone()
    if diagnosis is None:
        raise LookupError("尚无经营诊断报告，请先完成 AI 经营分析")
    if diagnosis["status"] != "confirmed":
        raise LookupError(f"最新经营诊断报告状态为 {diagnosis['status']}，必须先确认报告")
    current = collect_business_context(conn, cfg)
    if diagnosis["source_fingerprint"] != current["fingerprint"]:
        conn.execute("UPDATE business_diagnosis_reports SET status='stale' WHERE id=? AND status='confirmed'", (diagnosis["id"],))
        conn.commit()
        raise LookupError("经营资料或素材已变化，诊断报告已过期；请重新分析并确认")
    try:
        report = json.loads(diagnosis["report_json"])
        source_snapshot = json.loads(diagnosis["source_snapshot_json"])
    except (TypeError, ValueError, KeyError) as exc:
        raise LookupError("经营诊断报告数据损坏，无法生成母计划") from exc
    if not isinstance(report, dict) or not isinstance(source_snapshot, dict):
        raise LookupError("经营诊断报告结构无效，无法生成母计划")

    previous_plan, _, _, _ = PlanVersionManager(cfg, conn).load_plan()
    prompt_data = {
        "confirmed_diagnosis": report,
        "diagnosis_source_snapshot": source_snapshot,
        "current_business_context": current["context"],
        "previous_plan_reference": previous_plan,
        "supported_platforms": sorted(VALID_PLATFORMS),
        "current_schema": {
            "topic_count_per_platform": "integer 1..50",
            "dimension_allocation": "object of dimension name to non-negative integer; sum <= topic_count_per_platform",
            "enabled_platforms": "non-empty unique array of supported platforms",
            "content_format": "image_text only",
            "per_platform": "optional map of enabled platform to {topic_count: 1..topic_count_per_platform, publish_slots: [HH:MM]}",
            "cadence": "optional {round_interval_days: 0.5..90, rounds_planned: 1..200, goal_id: string}",
        },
    }
    prompt = (
        "你是严谨的经营策略与图文内容计划制定专家。仅使用已确认诊断和给定资料，不得编造事实。"
        "明确区分已证实事实、推断和未知项；不得把推测写成用户确认事实。"
        "制定适合当前业务目标、受众、产品/服务、内容定位、平台选择、内容方向分配、"
        "发布频率与生产产能约束的母计划。资源依据、风险、假设和待用户确认事项必须写入 summary，"
        "并明确指出它们是依据、推断或待确认。平台选择和频率必须受已登记账号、素材储备和产能约束。"
        "输出 JSON 对象，必须包含 plan、summary、assumptions、risks、pending_confirmations、resource_basis。"
        "plan 只能包含 current_schema 中的字段，严格遵守字段类型和范围；不要添加 schema 之外的键。"
        "plan.dimension_allocation 的维度应对应诊断报告中的内容方向。"
        "\\n输入数据：" + json.dumps(prompt_data, ensure_ascii=False, sort_keys=True, default=str)
    )
    generated, model = await _call_model(cfg, prompt)
    try:
        plan = validate_master_plan(generated["plan"], cfg)
    except (MasterPlanGenerationError, KeyError, TypeError, ValueError) as exc:
        raise MasterPlanGenerationError(str(exc)) from exc
    for field in ("summary", "assumptions", "risks", "pending_confirmations", "resource_basis"):
        if field not in generated:
            raise MasterPlanGenerationError(f"模型结果缺少 {field}")
    summary = generated["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise MasterPlanGenerationError("summary 必须是非空字符串")
    for field in ("assumptions", "risks", "pending_confirmations", "resource_basis"):
        if not isinstance(generated[field], (list, dict)):
            raise MasterPlanGenerationError(f"{field} 必须是数组或对象")

    # Prevent a long-running model response from being saved against changed evidence.
    after = collect_business_context(conn, cfg)
    latest_again = conn.execute(
        "SELECT id,status,source_fingerprint FROM business_diagnosis_reports ORDER BY version_no DESC LIMIT 1"
    ).fetchone()
    if (after["fingerprint"] != current["fingerprint"] or latest_again is None
            or int(latest_again["id"]) != int(diagnosis["id"])
            or latest_again["status"] != "confirmed"
            or latest_again["source_fingerprint"] != current["fingerprint"]):
        if latest_again is not None and latest_again["status"] == "confirmed":
            conn.execute("UPDATE business_diagnosis_reports SET status='stale' WHERE id=?", (latest_again["id"],))
            conn.commit()
        raise LookupError("生成期间诊断报告或业务资料发生变化，未创建母计划草稿；请重新分析")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    metadata = {
        "generation_source": "confirmed_business_diagnosis",
        "diagnosis_report_id": int(diagnosis["id"]),
        "diagnosis_version_no": int(diagnosis["version_no"]),
        "source_fingerprint": str(diagnosis["source_fingerprint"]),
        "model": model,
        "generated_at": now,
        "summary": summary.strip()[:4000],
        "assumptions": generated["assumptions"],
        "risks": generated["risks"],
        "pending_confirmations": generated["pending_confirmations"],
        "resource_basis": generated["resource_basis"],
    }
    change_note = "AI 母计划草稿：" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), default=str)
    active = conn.execute("SELECT id FROM master_plan_versions WHERE status='effective'").fetchone()
    base_version_id = int(active["id"]) if active else None
    try:
        draft = PlanVersionStore(conn).create_master_draft(
            plan, author="ai", source="system", based_on_version_id=base_version_id,
            change_note=change_note,
        )
    except PlanVersionError as exc:
        raise MasterPlanGenerationError(str(exc)) from exc
    return {
        "draft": draft,
        "generation": metadata,
        "requires_review": True,
        "effective": False,
    }
