"""Tenant-scoped business APIs for immutable platform subplans.

All writes create immutable draft versions. Only the explicit review endpoint activates a version.
"""
from __future__ import annotations

import json
import logging
import uuid
import os
import re
import httpx
import sqlite3
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.core.plan_version_store import PlanVersionError, PlanVersionStore

logger = logging.getLogger(__name__)


def _parse_subplan_model_content(raw: object) -> dict[str, Any]:
    """Extract one JSON object from OpenAI-compatible text or content-part responses."""
    if isinstance(raw, list):
        parts = []
        for part in raw:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        raw = "".join(parts)
    if not isinstance(raw, str):
        raise ValueError("模型响应缺少文本内容")
    start = raw.find("{")
    if start < 0:
        raise ValueError("模型未返回 JSON 对象")
    try:
        value, _end = json.JSONDecoder().raw_decode(raw[start:])
    except (TypeError, ValueError) as exc:
        raise ValueError("模型返回的 JSON 对象无效") from exc
    if not isinstance(value, dict) or not isinstance(value.get("subplans"), list):
        raise ValueError("模型返回的 JSON 缺少 subplans 数组")
    return value


def register_subplan_routes(app: FastAPI, get_runtime) -> None:
    def store() -> PlanVersionStore:
        runtime = get_runtime()
        return PlanVersionStore(runtime["conn"])

    def fail(exc: Exception):
        if isinstance(exc, PlanVersionError):
            message = str(exc)
            status = 404 if any(x in message for x in ("不存在",)) else 409
            raise HTTPException(status, message) from exc
        raise exc

    def _validate_stage_inputs(stages: list[dict[str, Any]]) -> None:
        if not stages:
            raise HTTPException(422, "子计划至少需要一个阶段")
        orders = []
        for index, stage in enumerate(stages, 1):
            if not isinstance(stage, dict) or not str(stage.get("title") or "").strip():
                raise HTTPException(422, "阶段标题不能为空")
            raw_target = stage.get("target_count", 1)
            raw_order = stage.get("stage_order", index)
            if raw_target is None:
                raw_target = 1
            if raw_order is None:
                raw_order = index
            if isinstance(raw_target, bool) or isinstance(raw_order, bool):
                raise HTTPException(422, "阶段目标数量和排序必须是整数")
            try:
                target = int(raw_target)
                order = int(raw_order)
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(422, "阶段目标数量和排序必须是整数")
            if str(raw_target).strip() != str(target) or str(raw_order).strip() != str(order):
                raise HTTPException(422, "阶段目标数量和排序必须是整数")
            if target < 1 or target > 10000 or order < 1:
                raise HTTPException(422, "阶段目标数量必须在 1-10000 之间，排序必须大于 0")
            orders.append(order)
            start, end = stage.get("start_date"), stage.get("end_date")
            for value in (start, end):
                if value:
                    try:
                        from datetime import date
                        date.fromisoformat(str(value))
                    except (TypeError, ValueError):
                        raise HTTPException(422, "阶段日期必须是 YYYY-MM-DD")
            if start and end and str(end) < str(start):
                raise HTTPException(422, "阶段结束日期不能早于开始日期")
        if len(set(orders)) != len(orders):
            raise HTTPException(422, "阶段排序不能重复")
        keys = [str(stage.get("stage_key") or "").strip() for stage in stages if stage.get("stage_key")]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, "阶段稳定标识不能重复")
        known = set(keys)
        graph = {}
        for stage in stages:
            key = str(stage.get("stage_key") or "").strip()
            deps = stage.get("dependencies", stage.get("depends_on", []))
            if not isinstance(deps, list) or any(not isinstance(dep, str) or not dep.strip() for dep in deps):
                raise HTTPException(422, "阶段依赖必须是稳定阶段标识数组")
            deps = [dep.strip() for dep in deps]
            if len(set(deps)) != len(deps) or (key and key in deps):
                raise HTTPException(422, "阶段依赖不能重复或依赖自身")
            if deps and (not key or any(dep not in known for dep in deps)):
                raise HTTPException(422, "阶段依赖必须引用同一版本中已定义的稳定阶段标识")
            graph[key] = deps
            if "entry_condition_met" in stage and not isinstance(stage["entry_condition_met"], bool):
                raise HTTPException(422, "entry_condition_met 必须是布尔值")
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting:
                raise HTTPException(422, "阶段依赖关系存在循环")
            if key in visited:
                return
            visiting.add(key)
            for dep in graph.get(key, []):
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in graph:
            visit(key)
    def _stages(version_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in get_runtime()["conn"].execute(
            "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order,id",
            (version_id,),
        ).fetchall()]

    def _version(version_id: int) -> dict[str, Any]:
        row = get_runtime()["conn"].execute(
            "SELECT * FROM sub_plan_versions WHERE id=?", (version_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "子计划版本不存在")
        result = dict(row)
        try:
            result["snapshot"] = json.loads(result.pop("snapshot_json"))
        except (TypeError, ValueError):
            raise HTTPException(409, "子计划版本快照损坏")
        result["stages"] = _stages(version_id)
        for stage in result["stages"]:
            try:
                stage["dependencies"] = json.loads(stage.get("dependencies_json") or "[]")
                stage["entry_condition_met"] = bool(stage.get("entry_condition_met", 1))
            except (TypeError, ValueError):
                raise HTTPException(409, "阶段依赖快照损坏")
        outcomes = get_runtime()["conn"].execute(
            """SELECT o.* FROM sub_plan_stage_outcomes o
               JOIN sub_plan_stages s ON s.stage_key=o.stage_key
               WHERE s.sub_plan_version_id=? AND o.sub_plan_id=?
               ORDER BY o.recorded_at DESC,o.id DESC""",
            (version_id, result["sub_plan_id"]),
        ).fetchall()
        latest = {}
        for outcome in outcomes:
            latest.setdefault(outcome["stage_key"], dict(outcome))
        for stage in result["stages"]:
            outcome = latest.get(stage.get("stage_key"))
            if outcome:
                try:
                    outcome["result"] = json.loads(outcome.pop("result_json"))
                except (TypeError, ValueError):
                    outcome["result"] = None
            stage["latest_outcome"] = outcome
        return result

    def _check_expected_version(version: dict[str, Any], expected: Any) -> None:
        if expected is None:
            raise HTTPException(428, "缺少 expected_version_no，请基于明确版本进行修改")
        if isinstance(expected, bool):
            raise HTTPException(422, "expected_version_no 必须是正整数")
        try:
            value = int(expected)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(422, "expected_version_no 必须是正整数")
        if str(expected).strip() != str(value) or value < 1:
            raise HTTPException(422, "expected_version_no 必须是正整数")
        latest = get_runtime()["conn"].execute(
            "SELECT MAX(version_no) FROM sub_plan_versions WHERE sub_plan_id=?",
            (version["sub_plan_id"],),
        ).fetchone()[0]
        if value != int(version["version_no"]) or value != int(latest or 0):
            raise HTTPException(409, "子计划版本已变化，请刷新后重试")

    def _create_revision(sub_plan_id: str, base_version_id: int | None,
                         snapshot: dict, stages: list[dict], note: str | None = None):
        conn = get_runtime()["conn"]
        identity = conn.execute(
            "SELECT master_plan_version_id FROM sub_plans WHERE id=?", (sub_plan_id,),
        ).fetchone()
        if identity is None:
            raise HTTPException(404, "子计划不存在")
        draft = store().create_subplan_version(
            sub_plan_id, snapshot,
            master_plan_version_id=int(identity["master_plan_version_id"]),
            author="user", stages=stages,
            based_on_version_id=base_version_id, change_note=note,
        )
        return draft

    @app.post("/api/subplans", status_code=201)
    def create_subplan(req: dict):
        master_id = req.get("master_plan_version_id")
        platform = str(req.get("platform") or "")
        title = str(req.get("title") or "").strip()
        stages = req.get("stages") or []
        snapshot = req.get("snapshot") or {"title": title, "platform": platform}
        if not isinstance(snapshot, dict) or not isinstance(stages, list) or not stages:
            raise HTTPException(422, "snapshot 必须为对象，stages 必须为非空数组")
        if snapshot.get("platform") not in (None, platform):
            raise HTTPException(422, "snapshot.platform 必须与子计划平台一致")
        snapshot["title"] = title
        snapshot["platform"] = platform
        master_row = get_runtime()["conn"].execute(
            "SELECT plan_snapshot FROM master_plan_versions WHERE id=? AND status='effective'",
            (master_id,),
        ).fetchone()
        if master_row is None:
            raise HTTPException(409, "关联主计划不存在或尚未生效")
        try:
            master_platforms = set(json.loads(master_row["plan_snapshot"]).get("enabled_platforms") or [])
        except (TypeError, ValueError):
            raise HTTPException(409, "生效主计划快照损坏")
        if platform not in master_platforms:
            raise HTTPException(422, "子计划平台必须属于生效主计划启用的平台")
        _validate_stage_inputs(stages)
        sub_id = str(req.get("id") or ("sub_" + uuid.uuid4().hex))
        identity_created = False
        # Keep the request-scoped connection available to the compensation branch.
        conn = get_runtime()["conn"]
        try:
            identity = store().create_subplan(
                sub_id, master_plan_version_id=int(master_id),
                platform=platform, title=title, author="user",
            )
            identity_created = True
            version = store().create_subplan_version(
                sub_id, snapshot, master_plan_version_id=int(master_id),
                author="user", stages=stages, change_note="初始手工草稿",
            )
            return {"subplan": identity, "draft": _version(int(version["id"]))}
        except (PlanVersionError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            # Identity and first version are written by separate store operations.
            # Compensate if version creation fails so no unusable orphan subplan remains.
            if identity_created:
                try:
                    conn.execute(
                        "DELETE FROM sub_plans WHERE id=? AND status='draft' AND current_version_id IS NULL",
                        (sub_id,),
                    )
                    conn.commit()
                except sqlite3.Error:
                    conn.rollback()
                    logger.exception("清理未完成的子计划身份失败")
            fail(exc)

    @app.get("/api/subplans")
    def list_subplans(
        master_plan_version_id: int | None = None,
        platform: str | None = None,
        status: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        conn = get_runtime()["conn"]
        where, args = [], []
        if master_plan_version_id is not None:
            where.append("p.master_plan_version_id=?"); args.append(master_plan_version_id)
        if platform:
            where.append("p.platform=?"); args.append(platform)
        if status:
            where.append("p.status=?"); args.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(conn.execute("SELECT COUNT(*) FROM sub_plans p" + clause, args).fetchone()[0])
        rows = conn.execute(
            """SELECT p.*,v.version_no AS current_version_no,v.status AS current_version_status,
                      m.status AS master_version_status
               FROM sub_plans p
               LEFT JOIN sub_plan_versions v ON v.id=p.current_version_id
               LEFT JOIN master_plan_versions m ON m.id=p.master_plan_version_id"""
            + clause + " ORDER BY p.updated_at DESC LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size],
        ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total,
                "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}

    @app.get("/api/subplans/{sub_plan_id}")
    def get_subplan(sub_plan_id: str):
        conn = get_runtime()["conn"]
        identity = conn.execute(
            """SELECT p.*,m.status AS master_version_status
               FROM sub_plans p LEFT JOIN master_plan_versions m ON m.id=p.master_plan_version_id
               WHERE p.id=?""", (sub_plan_id,),
        ).fetchone()
        if identity is None:
            raise HTTPException(404, "子计划不存在")
        versions = [int(row[0]) for row in conn.execute(
            "SELECT id FROM sub_plan_versions WHERE sub_plan_id=? ORDER BY version_no DESC",
            (sub_plan_id,),
        ).fetchall()]
        return {"subplan": dict(identity), "versions": [_version(v) for v in versions]}

    @app.patch("/api/subplans/{sub_plan_id}")
    def edit_subplan(sub_plan_id: str, req: dict):
        conn = get_runtime()["conn"]
        identity = conn.execute("SELECT * FROM sub_plans WHERE id=?", (sub_plan_id,)).fetchone()
        if identity is None:
            raise HTTPException(404, "子计划不存在")
        base_id = req.get("based_on_version_id") or identity["current_version_id"]
        if base_id is None:
            raise HTTPException(409, "请指定要修订的草稿版本")
        base = _version(int(base_id))
        if base["sub_plan_id"] != sub_plan_id:
            raise HTTPException(404, "子计划版本不存在")
        _check_expected_version(base, req.get("expected_version_no"))
        snapshot = req.get("snapshot", base["snapshot"])
        stages = req.get("stages", base["stages"])
        if not isinstance(snapshot, dict) or not isinstance(stages, list) or not stages:
            raise HTTPException(422, "snapshot 必须为对象，stages 必须为非空数组")
        if snapshot.get("platform") not in (None, identity["platform"]):
            raise HTTPException(422, "修订版本不能更改所属平台")
        snapshot = dict(snapshot)
        snapshot["platform"] = identity["platform"]
        _validate_stage_inputs(stages)
        try:
            draft = _create_revision(sub_plan_id, int(base_id), snapshot, stages, req.get("change_note"))
            return _version(int(draft["id"]))
        except (PlanVersionError, TypeError, ValueError) as exc:
            fail(exc)

    @app.post("/api/subplans/{sub_plan_id}/stages", status_code=201)
    def add_stage(sub_plan_id: str, req: dict):
        if not isinstance(req.get("stage"), dict):
            raise HTTPException(422, "stage 必须为对象")
        base = int(req.get("based_on_version_id") or 0)
        version = _version(base)
        if version["sub_plan_id"] != sub_plan_id:
            raise HTTPException(404, "子计划版本不存在")
        _check_expected_version(version, req.get("expected_version_no"))
        stages = [{k: v for k, v in s.items() if k not in ("id", "sub_plan_version_id", "created_at", "updated_at")}
                  for s in version["stages"]]
        new_stage = dict(req["stage"])
        new_stage.setdefault("stage_key", uuid.uuid4().hex)
        new_stage["stage_order"] = len(stages) + 1
        stages.append(new_stage)
        _validate_stage_inputs(stages)
        try:
            draft = _create_revision(sub_plan_id, base, version["snapshot"], stages, "新增阶段")
            return _version(int(draft["id"]))
        except (PlanVersionError, TypeError, ValueError) as exc:
            fail(exc)

    @app.patch("/api/subplans/{sub_plan_id}/stages/{stage_id}")
    def edit_stage(sub_plan_id: str, stage_id: int, req: dict):
        base = int(req.get("based_on_version_id") or 0)
        version = _version(base)
        if version["sub_plan_id"] != sub_plan_id:
            raise HTTPException(404, "子计划版本不存在")
        _check_expected_version(version, req.get("expected_version_no"))
        stages = [{k: v for k, v in s.items() if k not in ("id", "sub_plan_version_id", "created_at", "updated_at")}
                  for s in version["stages"]]
        index = next((i for i, s in enumerate(version["stages"]) if int(s["id"]) == stage_id), None)
        if index is None:
            raise HTTPException(404, "阶段不属于指定版本")
        stages[index].update(req.get("stage") or {})
        _validate_stage_inputs(stages)
        try:
            draft = _create_revision(sub_plan_id, base, version["snapshot"], stages, "编辑阶段")
            return _version(int(draft["id"]))
        except (PlanVersionError, TypeError, ValueError) as exc:
            fail(exc)

    @app.delete("/api/subplans/{sub_plan_id}/stages/{stage_id}")
    def delete_stage(sub_plan_id: str, stage_id: int, based_on_version_id: int = Query(...),
                     expected_version_no: int = Query(..., ge=1)):
        version = _version(based_on_version_id)
        if version["sub_plan_id"] != sub_plan_id:
            raise HTTPException(404, "子计划版本不存在")
        _check_expected_version(version, expected_version_no)
        stages = [{k: v for k, v in s.items() if k not in ("id", "sub_plan_version_id", "created_at", "updated_at")}
                  for s in version["stages"] if int(s["id"]) != stage_id]
        if len(stages) == len(version["stages"]):
            raise HTTPException(404, "阶段不属于指定版本")
        for i, stage in enumerate(stages, 1):
            stage["stage_order"] = i
        _validate_stage_inputs(stages)
        try:
            draft = _create_revision(sub_plan_id, based_on_version_id, version["snapshot"], stages, "删除阶段")
            return _version(int(draft["id"]))
        except (PlanVersionError, TypeError, ValueError) as exc:
            fail(exc)

    @app.post("/api/subplans/{sub_plan_id}/stages/reorder")
    def reorder_stages(sub_plan_id: str, req: dict):
        base = int(req.get("based_on_version_id") or 0)
        order = req.get("stage_ids")
        if (not isinstance(order, list)
                or any(isinstance(value, bool) or not str(value).isdigit() for value in order)):
            raise HTTPException(422, "stage_ids 必须是无重复正整数阶段 ID 数组")
        order = [int(value) for value in order]
        if len(set(order)) != len(order):
            raise HTTPException(422, "stage_ids 必须是无重复阶段 ID 数组")
        version = _version(base)
        if version["sub_plan_id"] != sub_plan_id:
            raise HTTPException(404, "子计划版本不存在")
        _check_expected_version(version, req.get("expected_version_no"))
        by_id = {int(s["id"]): s for s in version["stages"]}
        if set(map(int, order)) != set(by_id):
            raise HTTPException(422, "排序必须包含该版本全部阶段且不能包含其他阶段")
        stages = []
        for i, sid in enumerate(order, 1):
            stage = {k: v for k, v in by_id[int(sid)].items()
                     if k not in ("id", "sub_plan_version_id", "created_at", "updated_at")}
            stage["stage_order"] = i
            stages.append(stage)
        _validate_stage_inputs(stages)
        try:
            draft = _create_revision(sub_plan_id, base, version["snapshot"], stages, "阶段排序调整")
            return _version(int(draft["id"]))
        except (PlanVersionError, TypeError, ValueError) as exc:
            fail(exc)

    @app.post("/api/subplans/{sub_plan_id}/review")
    def review_subplan(sub_plan_id: str, req: dict):
        version_id = int(req.get("version_id") or 0)
        action = req.get("action", "approve")
        try:
            version = _version(version_id)
            if version["sub_plan_id"] != sub_plan_id:
                raise HTTPException(404, "子计划版本不存在")
            if action == "approve":
                if not version["stages"]:
                    raise HTTPException(422, "子计划至少需要一个阶段才能审核生效")
                result = store().activate_subplan_version(version_id, reviewer="user")
            elif action == "reject":
                result = store().reject_subplan_version(
                    version_id, reviewer="user", reason=str(req.get("reason") or ""),
                )
            else:
                raise HTTPException(422, "action 只能是 approve 或 reject")
            return {"version": dict(result), "authorization": "active" if action == "approve" else "not_authorized"}
        except (PlanVersionError, TypeError, ValueError) as exc:
            fail(exc)



    @app.post("/api/subplans/ai-draft", status_code=201)
    async def create_ai_subplan_drafts(req: dict):
        """Generate platform subplan suggestions; every result is persisted as draft only."""
        conn = get_runtime()["conn"]
        cfg = get_runtime()["cfg"]
        master_id = req.get("master_plan_version_id")
        master = conn.execute(
            "SELECT * FROM master_plan_versions WHERE id=? AND status='effective'",
            (master_id,),
        ).fetchone() if master_id is not None else conn.execute(
            "SELECT * FROM master_plan_versions WHERE status='effective' ORDER BY version_no DESC LIMIT 1"
        ).fetchone()
        if master is None:
            raise HTTPException(409, "没有可用的生效主计划，请先创建并审核主计划")
        try:
            master_snapshot = json.loads(master["plan_snapshot"])
        except (TypeError, ValueError):
            raise HTTPException(409, "生效主计划快照损坏")
        platforms = req.get("platforms") or master_snapshot.get("enabled_platforms") or []
        allowed = {"xhs", "douyin", "kuaishou", "wechat_channels"}
        platforms = list(dict.fromkeys(str(p) for p in platforms))
        if not platforms or any(p not in allowed for p in platforms):
            raise HTTPException(422, "platforms 必须是有效平台数组")
        enabled_master_platforms = set(master_snapshot.get("enabled_platforms") or [])
        if not set(platforms).issubset(enabled_master_platforms):
            raise HTTPException(422, "AI 拆解平台必须属于生效主计划启用的平台")
        outcome_context = {}
        conn = get_runtime()["conn"]
        for platform in platforms:
            active_subplan = conn.execute(
                """SELECT id FROM sub_plans WHERE platform=? AND status='active'
                   ORDER BY updated_at DESC LIMIT 1""",
                (platform,),
            ).fetchone()
            if active_subplan is None:
                continue
            history = conn.execute(
                """SELECT o.stage_key,s.title,o.result_json,o.evaluation,o.recorded_at
                   FROM sub_plan_stage_outcomes o
                   JOIN sub_plan_stages s ON s.id=o.stage_id
                   WHERE o.sub_plan_id=?
                   ORDER BY o.recorded_at DESC,o.id DESC LIMIT 30""",
                (active_subplan["id"],),
            ).fetchall()
            parsed_history = []
            for item in history:
                try:
                    actual_result = json.loads(item["result_json"])
                except (TypeError, ValueError):
                    actual_result = {"unreadable_result": True}
                parsed_history.append({
                    "stage_key": item["stage_key"], "stage_title": item["title"],
                    "actual_result": actual_result, "evaluation": item["evaluation"],
                    "recorded_at": item["recorded_at"],
                })
            if parsed_history:
                outcome_context[platform] = parsed_history

        prompt = (
            "你是内容运营计划拆解器。根据母计划，为每个平台分别生成子计划草稿。"
            "只返回 JSON 对象，格式为 {subplans:[{platform,title,snapshot,stages:["
            "{stage_key,title,direction,target_count,start_date,end_date,completion_condition,"
            "strategy_goal,current_problem,execution_strategy,prerequisites,input_info,"
            "expected_result,completion_criteria,effect_evaluation,entry_condition,"
            "entry_condition_met,dependencies,notes}]}]}。"
            "每个阶段必须有唯一 stage_key；dependencies 是前置阶段 stage_key 数组，只能引用本子计划中更早的阶段；"
            "不得生成循环依赖。前置阶段的实际结果将供后续阶段引用。"
            "每个子计划至少 1 个阶段；阶段顺序按数组顺序；target_count 是用户最终确认发布篇数，"
            "不是生成草稿数。不得输出审核状态或要求自动生效。"
            "\n母计划："
            + json.dumps(master_snapshot, ensure_ascii=False)
            + "\n目标平台：" + json.dumps(platforms, ensure_ascii=False)
            + "\n历史阶段实际结果：" + json.dumps(outcome_context, ensure_ascii=False)
            + "\n必须基于历史实际结果调整后续策略步骤：未达成预期时明确修正问题和执行策略；"
              "已达成时说明可复用做法及是否扩大规模；不得忽略历史结果或把历史结果伪装成新的预期结果。"
        )

        # Use the same independently configured primary/fallback providers as content
        # generation. The fallback must use its own base URL and API-key environment
        # variable; reusing the primary URL/key sends cross-provider credentials to
        # the wrong endpoint and makes the configured fallback ineffective.
        provider_specs = [
            {
                "name": str(getattr(cfg.llm, "provider", "primary") or "primary"),
                "base_url": str(cfg.llm.base_url or "").strip(),
                "model": str(cfg.llm.model or "").strip(),
                "api_key_env": str(cfg.llm.api_key_env or "").strip(),
                "thinking": str(getattr(cfg.llm, "thinking", "") or "").strip(),
            },
        ]
        fallback_model = str(getattr(cfg.llm, "fallback_model", "") or "").strip()
        if fallback_model:
            provider_specs.append({
                "name": str(getattr(cfg.llm, "fallback_provider", "fallback") or "fallback"),
                "base_url": str(getattr(cfg.llm, "fallback_base_url", "") or "").strip(),
                "model": fallback_model,
                "api_key_env": str(getattr(cfg.llm, "fallback_api_key_env", "") or "").strip(),
                "thinking": "",
            })

        attempts = []
        data: dict[str, Any] | None = None
        for spec in provider_specs:
            if not spec["base_url"] or not spec["model"] or not spec["api_key_env"]:
                attempts.append({"provider": spec["name"], "error": "provider_configuration_incomplete"})
                continue
            api_key = os.getenv(spec["api_key_env"])
            if not api_key:
                attempts.append({"provider": spec["name"], "error": "api_key_missing"})
                continue
            base_url = spec["base_url"].rstrip("/")
            request_url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
            request_body = {
                "model": spec["model"],
                "temperature": 0.25,
                "messages": [
                    {"role": "system", "content": "只输出合法 JSON，不要 Markdown。"},
                    {"role": "user", "content": prompt},
                ],
            }
            if spec["thinking"] in {"enabled", "disabled"}:
                request_body["thinking"] = {"type": spec["thinking"]}
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                    response = await client.post(
                        request_url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=request_body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    message = payload["choices"][0]["message"]
                    raw = message.get("content") if isinstance(message, dict) else None
                    candidate = _parse_subplan_model_content(raw)
                    data = candidate
                    break
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                # Record only provider name and exception class. Never expose keys,
                # request headers, full URLs, or raw model responses to API clients.
                attempts.append({"provider": spec["name"], "error": type(exc).__name__})
                logger.warning(
                    "AI 子计划拆解通道失败 provider=%s error=%s",
                    spec["name"], type(exc).__name__,
                )
        if not isinstance(data, dict) or not isinstance(data.get("subplans"), list):
            raise HTTPException(
                502, {"code": "ai_generation_failed", "message": "AI 拆解失败或返回格式不合法",
                      "manual_creation_available": True, "attempts": attempts},
            )
        suggestions = data["subplans"]
        returned_platforms = [
            item.get("platform") for item in suggestions if isinstance(item, dict)
        ]
        if (len(suggestions) != len(platforms)
                or len(returned_platforms) != len(platforms)
                or any(not isinstance(p, str) for p in returned_platforms)
                or set(returned_platforms) != set(platforms)):
            raise HTTPException(502, {"code": "ai_result_incomplete", "message": "AI 未完整覆盖所选平台",
                                      "manual_creation_available": True})
        normalized = []
        for item in suggestions:
            if not isinstance(item, dict) or item.get("platform") not in platforms:
                raise HTTPException(502, {"code": "ai_result_invalid", "message": "AI 返回了未请求的平台",
                                          "manual_creation_available": True})
            stages = item.get("stages")
            if not isinstance(stages, list) or not stages:
                raise HTTPException(502, {"code": "ai_stages_missing", "message": "AI 未生成阶段",
                                          "manual_creation_available": True})
            clean_stages = []
            for index, stage in enumerate(stages, 1):
                if not isinstance(stage, dict) or not str(stage.get("title") or "").strip():
                    raise HTTPException(502, {"code": "ai_stage_invalid", "message": "AI 阶段字段不完整",
                                              "manual_creation_available": True})
                try:
                    target = int(stage.get("target_count"))
                except (TypeError, ValueError):
                    target = 0
                if target < 1 or target > 10000:
                    raise HTTPException(502, {"code": "ai_target_invalid", "message": "AI 阶段目标数量不合理",
                                              "manual_creation_available": True})
                if "entry_condition_met" in stage and not isinstance(stage["entry_condition_met"], bool):
                    raise HTTPException(502, {"code": "ai_entry_condition_invalid", "message": "AI 阶段进入条件格式不合法",
                                              "manual_creation_available": True})
                clean_stages.append({
                    "stage_order": index, "stage_key": str(stage.get("stage_key") or ("ai-stage-" + uuid.uuid4().hex)),
                    "title": str(stage["title"]).strip(),
                    "direction": str(stage.get("direction") or ""),
                    "target_count": target, "start_date": stage.get("start_date"),
                    "end_date": stage.get("end_date"),
                    "completion_condition": str(stage.get("completion_condition") or stage.get("completion_criteria") or ""),
                    "completion_criteria": str(stage.get("completion_criteria") or stage.get("completion_condition") or ""),
                    "strategy_goal": str(stage.get("strategy_goal") or ""),
                    "current_problem": str(stage.get("current_problem") or ""),
                    "execution_strategy": str(stage.get("execution_strategy") or ""),
                    "prerequisites": str(stage.get("prerequisites") or ""),
                    "input_info": str(stage.get("input_info") or ""),
                    "expected_result": str(stage.get("expected_result") or ""),
                    "effect_evaluation": str(stage.get("effect_evaluation") or ""),
                    "entry_condition": str(stage.get("entry_condition") or ""),
                    "entry_condition_met": stage.get("entry_condition_met", True),
                    "dependencies": stage.get("dependencies") or [],
                    "notes": str(stage.get("notes") or ""),
                })
            title = str(item.get("title") or f"{item['platform']} 平台子计划").strip()
            snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else {}
            snapshot.update({"title": title, "platform": item["platform"],
                             "ai_generated": True, "master_plan_version_id": int(master["id"])})
            _validate_stage_inputs(clean_stages)
            normalized.append((item["platform"], title, snapshot, clean_stages))
        created = []
        try:
            for platform, title, snapshot, stages in normalized:
                sub_id = "sub_" + uuid.uuid4().hex
                identity = store().create_subplan(
                    sub_id, master_plan_version_id=int(master["id"]),
                    platform=platform, title=title, author="ai",
                )
                version = store().create_subplan_version(
                    sub_id, snapshot, master_plan_version_id=int(master["id"]),
                    author="ai", stages=stages, change_note="AI 拆解建议，待人工审核",
                )
                created.append({"subplan": identity, "draft": _version(int(version["id"]))})
        except (PlanVersionError, TypeError, ValueError) as exc:
            # AI decomposition is one logical batch. If any item fails validation or
            # persistence, remove every still-unapproved identity/version created by
            # this request rather than leaving a partially generated batch behind.
            try:
                for item in created:
                    created_id = item.get("subplan", {}).get("id")
                    if created_id:
                        conn.execute(
                            "DELETE FROM sub_plans WHERE id=? AND status='draft' AND current_version_id IS NULL",
                            (created_id,),
                        )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                logger.exception("清理失败的 AI 子计划拆解批次失败")
            fail(exc)
        return {"items": created, "effective": False, "requires_human_review": True,
                "manual_creation_available": True}


    @app.post("/api/subplans/{sub_plan_id}/stages/{stage_id}/outcomes", status_code=201)
    def record_stage_outcome(sub_plan_id: str, stage_id: int, req: dict):
        version_id = req.get("version_id")
        result = req.get("result")
        if isinstance(version_id, bool):
            raise HTTPException(422, "version_id 必须是正整数")
        try:
            version_id = int(version_id)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(422, "version_id 必须是正整数")
        if not isinstance(result, dict):
            raise HTTPException(422, "result 必须是实际执行结果对象")
        conn = get_runtime()["conn"]
        identity = conn.execute(
            "SELECT current_version_id FROM sub_plans WHERE id=?", (sub_plan_id,)
        ).fetchone()
        if identity is None:
            raise HTTPException(404, "子计划不存在")
        current_version_id = int(identity["current_version_id"] or 0)
        if current_version_id < 1:
            raise HTTPException(409, "子计划尚无生效版本，不能记录实际结果")
        requested_stage = conn.execute(
            """SELECT s.stage_key FROM sub_plan_stages s
               JOIN sub_plan_versions v ON v.id=s.sub_plan_version_id
               WHERE s.id=? AND v.id=? AND v.sub_plan_id=?""",
            (stage_id, version_id, sub_plan_id),
        ).fetchone()
        if requested_stage is None:
            raise HTTPException(404, "阶段不属于指定子计划版本")
        effective_stage = conn.execute(
            """SELECT id FROM sub_plan_stages
               WHERE sub_plan_version_id=? AND stage_key=?""",
            (current_version_id, requested_stage["stage_key"]),
        ).fetchone()
        if effective_stage is None:
            raise HTTPException(409, "当前生效版本已移除该策略步骤，不能记录其执行结果")
        try:
            outcome = store().record_stage_outcome(
                int(effective_stage["id"]), current_version_id, result,
                evaluation=str(req.get("evaluation") or ""), author="user",
            )
            return {"outcome": outcome}
        except PlanVersionError as exc:
            fail(exc)

    @app.post("/api/subplans/{sub_plan_id}/rounds", status_code=201)
    def create_subplan_round(sub_plan_id: str, req: dict):
        """Manual first/next round: still binds an approved stage and durable idempotency."""
        from src.core.timezone import business_now
        from src.core.subplan_production import production_paused
        runtime = get_runtime()
        conn = runtime["conn"]
        if production_paused(conn):
            raise HTTPException(409, "内容生产已暂停")
        identity = conn.execute(
            """SELECT p.*,v.status AS version_status,m.status AS master_status
               FROM sub_plans p JOIN sub_plan_versions v ON v.id=p.current_version_id
               JOIN master_plan_versions m ON m.id=p.master_plan_version_id
               WHERE p.id=?""", (sub_plan_id,),
        ).fetchone()
        if identity is None or identity["status"] != "active" or identity["version_status"] != "effective" or identity["master_status"] != "effective":
            raise HTTPException(409, "子计划未审核生效或授权已暂停")
        stages = conn.execute(
            """SELECT s.*,COALESCE(stats.confirmed_published_count,0) AS confirmed_published_count
               FROM sub_plan_stages s LEFT JOIN sub_plan_stage_publish_stats stats ON stats.stage_id=s.id
               WHERE s.sub_plan_version_id=? ORDER BY s.stage_order""",
            (identity["current_version_id"],),
        ).fetchall()
        stage = None
        lifecycle = store()
        for candidate in stages:
            if candidate["status"] in ("completed", "cancelled"):
                continue
            if candidate["status"] == "paused":
                raise HTTPException(409, "当前阶段已暂停")
            confirmed = int(candidate["confirmed_published_count"] or 0)
            if confirmed >= int(candidate["target_count"]):
                if candidate["status"] == "pending":
                    lifecycle.update_stage_status(int(candidate["id"]), "in_progress")
                lifecycle.update_stage_status(int(candidate["id"]), "completed")
                continue
            stage = candidate
            break
        if stage is None:
            counts = conn.execute(
                "SELECT COUNT(*),SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) "
                "FROM sub_plan_stages WHERE sub_plan_version_id=?",
                (identity["current_version_id"],),
            ).fetchone()
            total, completed, cancelled = (int(counts[0] or 0), int(counts[1] or 0), int(counts[2] or 0))
            if total and completed == total:
                conn.execute("UPDATE sub_plans SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (sub_plan_id,))
            elif total and completed + cancelled == total:
                conn.execute("UPDATE sub_plans SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (sub_plan_id,))
            conn.commit()
            raise HTTPException(409, "子计划没有待执行阶段，所有阶段已完成或取消")
        # Fail closed before acquiring an idempotency lease: the selected stage must
        # satisfy its entry condition and every predecessor must have a completed,
        # evaluated outcome. create_round_once rechecks this atomically before insert.
        try:
            ready_stage = lifecycle.assert_stage_ready(
                int(stage["id"]), int(identity["current_version_id"])
            )
        except PlanVersionError as exc:
            try:
                dependencies = json.loads(stage["dependencies_json"] or "[]")
            except (TypeError, ValueError):
                dependencies = []
            raise HTTPException(409, detail={
                "code": "strategy_step_blocked",
                "message": str(exc),
                "sub_plan_id": sub_plan_id,
                "stage_id": int(stage["id"]),
                "stage_key": stage["stage_key"],
                "dependencies": dependencies,
            }) from exc

        remaining = max(0, int(stage["target_count"]) - int(stage["confirmed_published_count"] or 0))
        if remaining <= 0:
            raise HTTPException(409, "当前阶段用户确认发布目标已达成")
        active_round = conn.execute(
            "SELECT id FROM spiral_rounds WHERE status IN "
            "('created','producing','awaiting_publish','collecting_metrics','analyzing') LIMIT 1"
        ).fetchone()
        if active_round:
            raise HTTPException(409, f"第 {active_round['id']} 轮仍在进行，不能并发开新轮")
        today = business_now(runtime["cfg"]).date().isoformat()
        if stage["start_date"] and today < stage["start_date"]:
            raise HTTPException(409, "当前阶段尚未到执行日期")
        if stage["end_date"] and today > stage["end_date"]:
            raise HTTPException(409, "当前阶段执行窗口已结束")
        broken = conn.execute(
            "SELECT 1 FROM circuit_breaker_events WHERE platform=? AND resolved_at IS NULL LIMIT 1",
            (identity["platform"],),
        ).fetchone()
        if broken:
            raise HTTPException(409, "子计划所属平台处于熔断状态，不能开轮")
        raw_count = req.get("topic_count")
        if raw_count is None:
            requested_count = int(runtime["cfg"].autogrow.topic_count)
        else:
            if isinstance(raw_count, bool):
                raise HTTPException(422, "topic_count 必须是 1-50 的整数")
            try:
                requested_count = int(raw_count)
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(422, "topic_count 必须是 1-50 的整数")
            if str(raw_count).strip() != str(requested_count):
                raise HTTPException(422, "topic_count 必须是 1-50 的整数")
        if requested_count < 1 or requested_count > 50:
            raise HTTPException(422, "topic_count 必须在 1-50 之间")
        topic_count = min(requested_count, remaining, 50)
        now_slot = business_now(runtime["cfg"]).replace(minute=0, second=0, microsecond=0).isoformat()
        key = f"round-open:{now_slot}"
        request_hash = __import__("hashlib").sha256(json.dumps(
            {"sub_plan_id": sub_plan_id,
             "sub_plan_version_id": identity["current_version_id"],
             "stage_id": stage["id"], "slot": now_slot,
             "topic_count": topic_count, "platforms": [identity["platform"]]},
            sort_keys=True, ensure_ascii=False,
        ).encode()).hexdigest()
        lease = None
        s = store()
        try:
            lease = s.reserve_round_open(
                key, request_hash,
                master_plan_version_id=int(identity["master_plan_version_id"]),
                sub_plan_id=sub_plan_id,
                sub_plan_version_id=int(identity["current_version_id"]),
                sub_plan_stage_id=int(stage["id"]),
            )
            if not lease["acquired"] and lease["status"] == "in_progress":
                raise HTTPException(409, "相同请求的生产轮次正在创建")
            if not lease["acquired"] and lease["status"] == "created":
                prior = conn.execute(
                    "SELECT status FROM spiral_rounds WHERE id=?", (lease.get("round_id"),),
                ).fetchone()
                if prior is not None and prior["status"] != "created":
                    raise HTTPException(409, "当前账号组本调度时段已经开过轮，请等待下一调度周期")
            goal = conn.execute(
                "SELECT id FROM goals WHERE status='active' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            created = s.create_round_once(
                key, goal_id=goal[0] if goal else None,
                content_variants=[{"platform": identity["platform"]}],
                trigger_source="manual",
            )
            if stage["status"] == "pending":
                s.update_stage_status(int(stage["id"]), "in_progress")
            round_id = int(created["round_id"])
            number = int(conn.execute("SELECT round_number FROM spiral_rounds WHERE id=?", (round_id,)).fetchone()[0])
            return {"round_id": round_id, "round_number": number, "deduped": created["deduped"],
                    "topic_count": topic_count, "platforms": [identity["platform"]],
                    "bound_subplan_version_id": identity["current_version_id"],
                    "bound_stage_id": stage["id"],
                    "dependency_outcomes": ready_stage.get("dependency_outcomes", {})}
        except HTTPException:
            raise
        except (PlanVersionError, TypeError, ValueError, sqlite3.Error) as exc:
            if lease and lease.get("acquired"):
                try:
                    s.fail_round_open(key, str(exc))
                except Exception:
                    logger.exception("手工开轮租约失败状态写入失败")
            if isinstance(exc, sqlite3.Error):
                raise HTTPException(503, "数据库暂时无法创建生产轮次，请稍后重试") from exc
            fail(exc)


    @app.get("/api/production-monitor")
    def production_monitor():
        """Read-only hierarchy monitor for the effective master plan and its production lineage."""
        from datetime import datetime, timezone

        conn = get_runtime()["conn"]
        table_names = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        view_names = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()}
        stage_columns = {row["name"] for row in conn.execute(
            "PRAGMA table_info(sub_plan_stages)"
        ).fetchall()}
        draft_columns = {row["name"] for row in conn.execute(
            "PRAGMA table_info(drafts)"
        ).fetchall()}
        dependency_fields_available = "dependencies_json" in stage_columns or bool(
            {"prerequisites","dependencies","depends_on"} & stage_columns
        )
        # Migration 137 introduces the independent per-content production task table.
        production_tasks_available = "production_tasks" in table_names
        master_rows = conn.execute(
            """SELECT id,version_no,plan_signature,status,created_at,effective_at
               FROM master_plan_versions WHERE status='effective'
               ORDER BY version_no DESC,id DESC LIMIT 2"""
        ).fetchall()
        consistency = []
        if len(master_rows) > 1:
            consistency.append({"code":"multiple_effective_master_plans","severity":"critical",
                                "count":len(master_rows),"message":"存在多个生效母计划版本"})
        master = dict(master_rows[0]) if master_rows else None
        platforms = []
        totals = {
            "subplans":0,"stages":0,"rounds":0,"production_tasks":0,"contents":0,"target_contents":0,
            "drafts":0,"review_pending":0,"review_approved":0,"review_rejected":0,"published":0,
            "failed_rounds":0,"successful_rounds":0,"failed_drafts":0,"metric_records":0
        }
        now = datetime.now(timezone.utc)
        if master:
            subplans = conn.execute(
                """SELECT p.id,p.platform,p.title,p.status,p.master_plan_version_id,
                          p.current_version_id,p.created_at,p.updated_at,
                          v.version_no,v.status AS version_status
                   FROM sub_plans p
                   LEFT JOIN sub_plan_versions v ON v.id=p.current_version_id
                   WHERE p.master_plan_version_id=?
                   ORDER BY p.platform,p.created_at,p.id""",
                (master["id"],)
            ).fetchall()
            for sub in subplans:
                subplan = dict(sub)
                totals["subplans"] += 1
                stages = []
                if subplan["current_version_id"] is not None:
                    stage_rows = conn.execute(
                        """SELECT s.*,COALESCE(pub.confirmed_published_count,0) AS published_view_count
                           FROM sub_plan_stages s
                           LEFT JOIN sub_plan_stage_publish_stats pub ON pub.stage_id=s.id
                           WHERE s.sub_plan_version_id=? ORDER BY s.stage_order,s.id""",
                        (subplan["current_version_id"],)
                    ).fetchall() if "sub_plan_stage_publish_stats" in view_names else conn.execute(
                        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order,id",
                        (subplan["current_version_id"],)
                    ).fetchall()
                    for stage_row in stage_rows:
                        stage = dict(stage_row)
                        stage_id = stage["id"]
                        if stage.get("stage_key") and "stage_key" in stage_columns:
                            round_rows = conn.execute(
                                """SELECT r.id,r.round_number,r.status,r.created_at,r.trigger_source,
                                          r.plan_attribution_status
                                   FROM spiral_rounds r
                                   WHERE r.sub_plan_id=? AND r.sub_plan_stage_id IN
                                     (SELECT id FROM sub_plan_stages WHERE stage_key=?)
                                   ORDER BY r.created_at DESC,r.id DESC""",
                                (subplan["id"],stage["stage_key"])
                            ).fetchall()
                        else:
                            round_rows = conn.execute(
                                """SELECT r.id,r.round_number,r.status,r.created_at,r.trigger_source,
                                          r.plan_attribution_status
                                   FROM spiral_rounds r
                                   WHERE r.sub_plan_id=? AND r.sub_plan_version_id=? AND r.sub_plan_stage_id=?
                                   ORDER BY r.created_at DESC,r.id DESC""",
                                (subplan["id"],subplan["current_version_id"],stage_id)
                            ).fetchall()
                        raw_dependencies = stage.get(
                            "dependencies_json",
                            stage.get("prerequisites",stage.get("dependencies",stage.get("depends_on",[]))),
                        )
                        if isinstance(raw_dependencies,str):
                            try:
                                raw_dependencies = json.loads(raw_dependencies)
                            except (TypeError,ValueError):
                                raw_dependencies = [raw_dependencies] if raw_dependencies.strip() else []
                        stage["dependencies"] = raw_dependencies if isinstance(raw_dependencies,list) else []
                        stage["dependencies_available"] = dependency_fields_available
                        round_ids = [int(r["id"]) for r in round_rows]
                        round_stats = {key:0 for key in (
                            "created","producing","awaiting_publish","collecting_metrics","analyzing",
                            "completed","partial_completed","failed","cancelled"
                        )}
                        content_count = draft_count = review_pending = review_approved = review_rejected = published = failed_drafts = 0
                        metric_records = 0
                        effect_summary = {"views":None,"likes":None,"collects":None,"comments":None,"shares":None,"new_followers":None}
                        for rr in round_rows:
                            status = str(rr["status"] or "")
                            if status in round_stats:
                                round_stats[status] += 1
                            if status == "failed":
                                totals["failed_rounds"] += 1
                            if status in {"completed","partial_completed"}:
                                totals["successful_rounds"] += 1
                            if status in {"producing","awaiting_publish"} and rr["created_at"]:
                                try:
                                    started = datetime.fromisoformat(str(rr["created_at"]).replace("Z","+00:00"))
                                    if started.tzinfo is None:
                                        started = started.replace(tzinfo=timezone.utc)
                                    age_seconds = (now-started).total_seconds()
                                    if status == "producing" and age_seconds > 1800:
                                        consistency.append({"code":"stuck_round","severity":"warning",
                                            "round_id":int(rr["id"]),"stage_id":stage_id,
                                            "message":f"轮次 {rr['round_number']} 已生产中超过 30 分钟"})
                                    if status == "awaiting_publish" and age_seconds > 48*3600:
                                        consistency.append({"code":"stuck_publish","severity":"warning",
                                            "round_id":int(rr["id"]),"stage_id":stage_id,
                                            "message":f"轮次 {rr['round_number']} 已待发布超过 48 小时"})
                                except (TypeError,ValueError):
                                    pass
                        content_items = []
                        if round_ids:
                            placeholders = ",".join("?" for _ in round_ids)
                            content_count = int(conn.execute(
                                f"SELECT COUNT(*) FROM contents WHERE round_id IN ({placeholders})",round_ids
                            ).fetchone()[0])
                            content_items = [dict(row) for row in conn.execute(
                                f"""SELECT c.id,c.title,c.platform,c.created_at,c.review_rejected,
                                           COUNT(d.id) AS draft_count,
                                           GROUP_CONCAT(d.status, ',') AS draft_statuses
                                    FROM contents c LEFT JOIN drafts d ON d.content_id=c.id
                                    WHERE c.round_id IN ({placeholders})
                                    GROUP BY c.id,c.title,c.platform,c.created_at,c.review_rejected
                                    ORDER BY c.created_at DESC,c.id DESC LIMIT 20""",
                                round_ids
                            ).fetchall()]
                            failed_expr = (
                                "status='failed' OR publish_status='failed'"
                                if "publish_status" in draft_columns else "status='failed'"
                            )
                            published_expr = (
                                "publish_status='published'"
                                if "publish_status" in draft_columns else "user_confirmed_at IS NOT NULL"
                            )
                            draft_row = conn.execute(
                                f"""SELECT COUNT(*) AS total,
                                           SUM(CASE WHEN {failed_expr} THEN 1 ELSE 0 END) AS failed,
                                           SUM(CASE WHEN {published_expr} THEN 1 ELSE 0 END) AS published
                                    FROM drafts WHERE content_id IN
                                      (SELECT id FROM contents WHERE round_id IN ({placeholders}))""",
                                round_ids
                            ).fetchone()
                            draft_count = int(draft_row["total"] or 0)
                            failed_drafts = int(draft_row["failed"] or 0)
                            published = int(draft_row["published"] or 0)
                            if "review_status" in draft_columns:
                                review_status_expr = (
                                    "CASE WHEN c.review_rejected = 1 THEN 'rejected' "
                                    "ELSE COALESCE(d.review_status,'pending') END"
                                )
                            else:
                                review_status_expr = (
                                    "CASE WHEN c.review_rejected = 1 THEN 'rejected' "
                                    "WHEN (SELECT u.override_field FROM user_override u "
                                    "WHERE u.round_id = c.round_id "
                                    "AND u.override_field IN ('review_approve','review_reject','review_resubmit') "
                                    "AND CASE WHEN json_valid(u.original_value) "
                                    "THEN json_extract(u.original_value, '$.content_id') ELSE NULL END = c.id "
                                    "ORDER BY u.overridden_at DESC, u.rowid DESC LIMIT 1) = 'review_approve' "
                                    "THEN 'approved' ELSE 'pending' END"
                                )
                            review_row = conn.execute(
                                f"""SELECT
                                        COUNT(DISTINCT CASE WHEN ({review_status_expr})='pending' THEN c.id END) AS pending,
                                        COUNT(DISTINCT CASE WHEN ({review_status_expr})='approved' THEN c.id END) AS approved,
                                        COUNT(DISTINCT CASE WHEN ({review_status_expr})='rejected' THEN c.id END) AS rejected
                                    FROM contents c JOIN drafts d ON d.content_id=c.id
                                    WHERE c.round_id IN ({placeholders})""",
                                round_ids
                            ).fetchone()
                            review_pending = int(review_row["pending"] or 0)
                            review_approved = int(review_row["approved"] or 0)
                            review_rejected = int(review_row["rejected"] or 0)
                            if "metrics" in table_names:
                                metric_row = conn.execute(
                                    f"""WITH ranked_metrics AS (
                                           SELECT m.*,
                                                  ROW_NUMBER() OVER (
                                                      PARTITION BY COALESCE(m.draft_id,m.content_id)
                                                      ORDER BY COALESCE(m.fetched_at,'') DESC,m.id DESC
                                                  ) AS row_no
                                           FROM metrics m
                                           WHERE m.content_id IN
                                             (SELECT id FROM contents WHERE round_id IN ({placeholders}))
                                             AND m.fetch_status IN ('success','partial')
                                       )
                                       SELECT COUNT(*) AS records,
                                              SUM(views) AS views,SUM(likes) AS likes,
                                              SUM(collects) AS collects,SUM(comments) AS comments,
                                              SUM(shares) AS shares,SUM(new_followers) AS new_followers
                                       FROM ranked_metrics WHERE row_no=1""",
                                    round_ids
                                ).fetchone()
                                metric_records = int(metric_row["records"] or 0)
                                effect_summary = {key:metric_row[key] for key in effect_summary}
                        task_status_counts = {}
                        if production_tasks_available and round_ids:
                            task_rows = conn.execute(
                                f"""SELECT status,COUNT(*) AS count FROM production_tasks
                                    WHERE strategy_step_id=? AND sub_plan_id=?
                                      AND round_id IN ({','.join('?' for _ in round_ids)})
                                    GROUP BY status""",
                                [str(stage.get("stage_key") or ""), str(subplan["id"]), *round_ids]
                            ).fetchall()
                            task_status_counts = {str(row["status"]):int(row["count"] or 0) for row in task_rows}
                        elif "task_runs" in table_names and round_ids:
                            # Legacy fallback is a round-pipeline run count, not a count of
                            # independently persisted content-production tasks.
                            task_rows = conn.execute(
                                f"""SELECT status,COUNT(*) AS count FROM task_runs
                                    WHERE task_type='round_pipeline'
                                      AND biz_id IN ({','.join('?' for _ in round_ids)})
                                    GROUP BY status""",
                                [str(rid) for rid in round_ids]
                            ).fetchall()
                            task_status_counts = {
                                ("succeeded" if row["status"] in ("success","completed") else str(row["status"])):
                                int(row["count"] or 0) for row in task_rows
                            }
                        task_count = sum(task_status_counts.values())
                        stage["round_count"] = len(round_rows)
                        stage["round_status_counts"] = round_stats
                        stage["production_task_count"] = task_count
                        stage["production_task_status_counts"] = task_status_counts
                        stage["content_count"] = content_count
                        stage["draft_count"] = draft_count
                        stage["review_pending_count"] = review_pending
                        stage["review_approved_count"] = review_approved
                        stage["review_rejected_count"] = review_rejected
                        stage["published_count"] = published
                        if stage.get("published_view_count") is not None and int(stage["published_view_count"]) != published:
                            consistency.append({"code":"stage_publish_count_mismatch","severity":"warning",
                                "stage_id":stage_id,"sub_plan_id":subplan["id"],
                                "count":abs(int(stage["published_view_count"])-published),
                                "message":f"策略步骤「{stage['title']}」发布统计视图与草稿明细不一致"})
                        stage["failed_draft_count"] = failed_drafts
                        stage["metric_record_count"] = metric_records
                        stage["effect_summary"] = effect_summary
                        stage["rounds"] = [dict(r) for r in round_rows[:20]]
                        stage["contents"] = content_items
                        if stage["status"] == "in_progress" and stage.get("updated_at"):
                            try:
                                updated = datetime.fromisoformat(str(stage["updated_at"]).replace("Z","+00:00"))
                                if updated.tzinfo is None:
                                    updated = updated.replace(tzinfo=timezone.utc)
                                if (now-updated).total_seconds() > 7*86400:
                                    consistency.append({"code":"stale_stage","severity":"warning",
                                        "stage_id":stage_id,"sub_plan_id":subplan["id"],
                                        "message":f"策略步骤「{stage['title']}」已超过 7 天未更新"})
                            except (TypeError,ValueError):
                                pass
                        totals["stages"] += 1
                        totals["rounds"] += len(round_rows)
                        totals["production_tasks"] += task_count
                        totals["contents"] += content_count
                        totals["target_contents"] += max(0,int(stage.get("target_count") or 0))
                        totals["drafts"] += draft_count
                        totals["review_pending"] += review_pending
                        totals["review_approved"] += review_approved
                        totals["review_rejected"] += review_rejected
                        totals["published"] += published
                        totals["failed_drafts"] += failed_drafts
                        totals["metric_records"] += metric_records
                        stages.append(stage)
                subplan["stages"] = stages
                subplan["stage_count"] = len(stages)
                subplan["round_count"] = sum(s["round_count"] for s in stages)
                platforms.append(subplan)
        if "spiral_rounds" in table_names:
            unresolved = int(conn.execute(
                "SELECT COUNT(*) FROM spiral_rounds WHERE plan_attribution_status IN ('pending','manual_review')"
            ).fetchone()[0]) if "plan_attribution_status" in {
                row["name"] for row in conn.execute("PRAGMA table_info(spiral_rounds)").fetchall()
            } else 0
            if unresolved:
                consistency.append({"code":"unresolved_round_attribution","severity":"warning",
                    "count":unresolved,"message":"存在无法准确归属母计划/子计划的历史轮次"})
        totals["failed_rounds"] = int(conn.execute(
            """SELECT COUNT(*) FROM spiral_rounds r
               JOIN sub_plans p ON p.id=r.sub_plan_id
               WHERE p.master_plan_version_id=? AND r.status='failed'""",
            (master["id"],)
        ).fetchone()[0]) if master else 0
        totals["abnormal_rounds"] = sum(1 for item in consistency if item["code"] in {"stuck_round","stuck_publish"})
        if totals["stages"] and not dependency_fields_available:
            consistency.append({"code":"strategy_step_dependencies_unavailable","severity":"warning",
                "count":totals["stages"],"message":"当前策略步骤数据模型尚未持久化前置依赖，无法展示依赖链"})
        target_contents = totals["target_contents"]
        totals["content_progress_percent"] = round(totals["contents"] / target_contents * 100, 1) if target_contents > 0 else None
        return {
            "master_plan":master,"platforms":platforms,"totals":totals,
            "consistency_checks":consistency,
            "generated_at":now.isoformat(timespec="seconds"),
            "data_scope":"current_tenant_effective_master_plan",
            "effect_summary": {
                key: (lambda values: sum(values) if values else None)(
                    [stage["effect_summary"][key] for sub in platforms for stage in sub["stages"]
                     if stage.get("effect_summary",{}).get(key) is not None]
                )
                for key in ("views","likes","collects","comments","shares","new_followers")
            },
            "limitations":{
                "production_task_source": (
                    "production_tasks" if production_tasks_available
                    else ("task_runs.round_pipeline" if "task_runs" in table_names else "unavailable")
                ),
                "task_model_available": production_tasks_available,
                "legacy_task_run_fallback": not production_tasks_available and "task_runs" in table_names,
                "metric_records_source":"metrics",
                "published_definition": (
                    "drafts.publish_status='published'"
                    if "publish_status" in draft_columns else "drafts.user_confirmed_at IS NOT NULL"
                )
            }
        }

    @app.post("/api/production/pause")
    def set_production_pause(req: dict):
        paused = req.get("paused")
        if not isinstance(paused, bool):
            raise HTTPException(422, "paused 必须是布尔值")
        conn = get_runtime()["conn"]
        conn.execute(
            """INSERT INTO app_settings(key,value,updated_at,updated_by)
               VALUES('production_paused',?,?, 'user')
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                   updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            ("1" if paused else "0", __import__("datetime").datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return {"paused": paused}

    @app.get("/api/production/pause")
    def get_production_pause():
        from src.core.subplan_production import production_paused
        return {"paused": production_paused(get_runtime()["conn"])}

    @app.post("/api/subplans/{sub_plan_id}/pause")
    def pause_subplan(sub_plan_id: str, req: dict):
        paused = req.get("paused", True)
        if not isinstance(paused, bool):
            raise HTTPException(422, "paused 必须是布尔值")
        conn = get_runtime()["conn"]
        if not paused:
            current = conn.execute(
                """SELECT p.status,v.status AS version_status,m.status AS master_status
                   FROM sub_plans p
                   JOIN sub_plan_versions v ON v.id=p.current_version_id
                   JOIN master_plan_versions m ON m.id=p.master_plan_version_id
                   WHERE p.id=?""", (sub_plan_id,),
            ).fetchone()
            if current is None:
                raise HTTPException(404, "子计划不存在或没有当前生效版本")
            if current["version_status"] != "effective" or current["master_status"] != "effective":
                raise HTTPException(409, "关联主计划版本已失效，不能恢复该子计划；请基于当前主计划重新创建并审核")
        cur = conn.execute(
            """UPDATE sub_plans
               SET status=?,
                   pause_reason=CASE WHEN ?='paused' THEN 'user' ELSE NULL END,
                   paused_by_master_version_id=NULL,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status IN ('active','paused')""",
            ("paused" if paused else "active", "paused" if paused else "active", sub_plan_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(404, "可暂停/恢复的子计划不存在")
        conn.commit()
        return {"sub_plan_id": sub_plan_id, "status": "paused" if paused else "active"}

    @app.get("/api/production-line")
    def production_line(
        sub_plan_id: str | None = None, platform: str | None = None,
        stage_id: int | None = None, version_id: int | None = None,
        round_status: str | None = None, page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
    ):
        from src.core.subplan_production import list_production_line
        conn = get_runtime()["conn"]
        items = list_production_line(
            conn, sub_plan_id=sub_plan_id, platform=platform, stage_id=stage_id,
            version_id=version_id, round_status=round_status,
            limit=page_size, offset=(page - 1) * page_size,
        )
        clauses, args = [], []
        if sub_plan_id:
            clauses.append("r.sub_plan_id=?"); args.append(sub_plan_id)
        if platform:
            clauses.append("p.platform=?"); args.append(platform)
        if stage_id is not None:
            clauses.append("r.sub_plan_stage_id=?"); args.append(stage_id)
        if version_id is not None:
            clauses.append("r.sub_plan_version_id=?"); args.append(version_id)
        if round_status:
            clauses.append("r.status=?"); args.append(round_status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(conn.execute(
            "SELECT COUNT(*) FROM spiral_rounds r LEFT JOIN sub_plans p ON p.id=r.sub_plan_id" + where,
            args,
        ).fetchone()[0])
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size),
                "has_more": page * page_size < total}

    @app.get("/api/rounds/{round_id}/plan-attribution")
    def round_plan_attribution(round_id: int):
        conn = get_runtime()["conn"]
        row = conn.execute(
            """SELECT id,master_plan_version_id,sub_plan_id,sub_plan_version_id,
                      sub_plan_stage_id,sub_plan_snapshot,sub_plan_stage_snapshot,
                      plan_attribution_status
               FROM spiral_rounds WHERE id=?""", (round_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "生产轮次不存在")
        result = dict(row)
        for field in ("sub_plan_snapshot", "sub_plan_stage_snapshot"):
            if result[field]:
                try:
                    result[field] = json.loads(result[field])
                except (TypeError, ValueError):
                    result[field] = None
        return result

    @app.get("/api/subplans/{sub_plan_id}/progress")
    def subplan_progress(sub_plan_id: str):
        conn = get_runtime()["conn"]
        identity = conn.execute("SELECT * FROM sub_plans WHERE id=?", (sub_plan_id,)).fetchone()
        if identity is None:
            raise HTTPException(404, "子计划不存在")
        current_id = identity["current_version_id"]
        if current_id is None:
            return {"sub_plan_id": sub_plan_id, "status": identity["status"], "stages": [], "confirmed_published_count": 0}
        stages = conn.execute(
            """SELECT s.*,COALESCE(p.confirmed_published_count,0) AS confirmed_published_count
               FROM sub_plan_stages s LEFT JOIN sub_plan_stage_publish_stats p ON p.stage_id=s.id
               WHERE s.sub_plan_version_id=? ORDER BY s.stage_order""", (current_id,),
        ).fetchall()
        items = []
        current_stage = None
        for row in stages:
            item = dict(row)
            item["remaining_count"] = max(0, int(item["target_count"]) - int(item["confirmed_published_count"]))
            if current_stage is None and item["status"] not in ("completed", "cancelled"):
                current_stage = item["id"]
            items.append(item)
        return {"sub_plan_id": sub_plan_id, "version_id": current_id,
                "status": identity["status"], "current_stage_id": current_stage,
                "confirmed_published_count": sum(x["confirmed_published_count"] for x in items),
                "stages": items}

    @app.get("/api/production-tasks")
    def list_production_tasks(
        round_id: int | None = None,
        strategy_step_id: str | None = None,
        status: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ):
        from src.core.production_task_store import ProductionTaskStore
        conn = get_runtime()["conn"]
        store = ProductionTaskStore(conn)
        items = store.list_tasks(
            round_id=round_id, strategy_step_id=strategy_step_id, status=status,
            limit=page_size, offset=(page - 1) * page_size,
        )
        clauses, args = [], []
        for column, value in (
            ("round_id", round_id), ("strategy_step_id", strategy_step_id), ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(conn.execute("SELECT COUNT(*) FROM production_tasks" + where, args).fetchone()[0])
        return {
            "items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "has_more": page * page_size < total,
        }

    @app.get("/api/production-tasks/{task_id}")
    def get_production_task(task_id: str):
        from src.core.production_task_store import ProductionTaskError, ProductionTaskStore
        conn = get_runtime()["conn"]
        store = ProductionTaskStore(conn)
        try:
            task = store.get_task(task_id)
        except ProductionTaskError as exc:
            raise HTTPException(404, str(exc)) from exc
        attempts = conn.execute(
            "SELECT * FROM production_task_attempts WHERE task_id=? ORDER BY attempt_no",
            (task_id,),
        ).fetchall()
        artifact = store.get_content_for_task(task_id)
        return {
            "task": task,
            "attempts": [dict(row) for row in attempts],
            "content": artifact,
        }

    @app.post("/api/production-tasks/{task_id}/recover")
    def recover_production_task_endpoint(
        task_id: str,
        confirmed_no_external_side_effect: bool = False,
    ):
        from src.core.production_task_store import ProductionTaskError
        from src.core.production_task_recovery import recover_production_task
        conn = get_runtime()["conn"]
        try:
            return recover_production_task(
                conn, task_id,
                confirmed_no_external_side_effect=confirmed_no_external_side_effect,
            )
        except ProductionTaskError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/production-tasks/recovery/reconcile-expired")
    def reconcile_expired_production_tasks_endpoint(limit: int = Query(200, ge=1, le=1000)):
        from src.core.production_task_recovery import reconcile_expired_production_tasks
        conn = get_runtime()["conn"]
        return {
            "items": reconcile_expired_production_tasks(conn, limit=limit),
        }
