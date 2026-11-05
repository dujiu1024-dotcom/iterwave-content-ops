"""T8-E API acceptance: confirmed diagnosis to an attributed production task."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine
from src.core.task_queue import GlobalTaskQueue
from src.core.production_task_store import ProductionTaskStore


@pytest.fixture()
def workflow_client(cfg, conn, test_auth):
    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "cfg": cfg, "conn": conn,
        "sm_draft": DraftStateMachine(conn),
        "sm_round": RoundStateMachine(conn),
        "plan_version_manager": PlanVersionManager(cfg, conn),
        "normalizer": Normalizer(conn, cfg), "notifier": Notifier(cfg, conn),
    })
    try:
        yield test_auth(TestClient(api_main._build_app(), base_url="http://127.0.0.1")), conn
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


async def _fake_diagnosis(conn, cfg):
    from src.core.business_diagnosis import collect_business_context
    context = collect_business_context(conn, cfg)
    return (
        {"business_summary": {"claim": "T8-E 测试业务", "classification": "fact"},
         "assumptions": [], "source_references": []},
        context["context"], "t8-e-test-model",
    )


@pytest.mark.asyncio
async def test_confirmed_diagnosis_to_production_task_api_happy_path(workflow_client, monkeypatch, tmp_path):
    from src.api import business_diagnosis
    from src.core import master_plan_orchestrator

    client, conn = workflow_client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *args, **kwargs: {})
    api_main._g_ctx["runtime_tenant_id"] = "default"
    monkeypatch.setattr(business_diagnosis, "generate_diagnosis", _fake_diagnosis)

    profile = client.put("/api/business-profile", json={
        "business_name": "T8-E API 验收业务", "industry": "测试行业",
        "business_model": "图文内容运营", "business_stage": "测试阶段",
        "operating_goal": "完成图文内容生产",
    })
    assert profile.status_code == 200, profile.text

    offering = client.post("/api/business-profile/items/service", json={
        "name": "T8-E 图文内容运营服务",
        "details": {
            "service_type": "图文内容运营",
            "introduction": "用于验证生产上下文中的业务服务快照",
            "delivery_method": "线上内容策划与制作",
        },
    })
    assert offering.status_code == 200, offering.text

    asset = client.post(
        "/api/original-assets",
        content=json.dumps({
            "asset_type": "product_image", "title": "T8-E 已授权图文素材",
            "description": "完整 API 主路径验收", "source_kind": "manual",
            "source_version": "fixture-v1",
            "metadata": {"fixture": "t8-e", "media_type": "image"},
        }, ensure_ascii=False),
        headers={"content-type": "application/json"},
    )
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["id"]
    reviewed_asset = client.post(
        f"/api/original-assets/{asset_id}/review",
        content=json.dumps({
            "decision": "confirmed", "review_note": "确认许可、隐私与质量",
            "quality": {
                "commercial_use_status": "allowed", "privacy_status": "clear",
                "readability_status": "readable", "completeness_status": "complete",
                "duplicate_status": "unique",
            },
        }, ensure_ascii=False),
        headers={"content-type": "application/json"},
    )
    assert reviewed_asset.status_code == 200, reviewed_asset.text

    north_star = client.put("/api/north-star", json={
        "change_reason": "T8-E 验收建立账号组北极星",
        "snapshot": {
            "positioning": "测试业务定位",
            "business_direction": "图文内容运营",
            "target_users": "目标测试用户",
            "core_value": "稳定生产高质量内容",
            "long_term_content_direction": "围绕核心业务持续输出",
            "business_model": "内容营销",
            "long_term_goals": "建立可持续内容生产体系",
        },
    })
    assert north_star.status_code == 200, north_star.text
    # Assert the North Star is durably visible to the same tenant/runtime before
    # allowing master-plan creation; a successful PUT response alone is insufficient.
    current_north_star = client.get("/api/north-star")
    assert current_north_star.status_code == 200, current_north_star.text
    assert current_north_star.json()["current"]["status"] == "effective"
    assert conn.execute(
        "SELECT COUNT(*) FROM account_group_north_star_versions WHERE status='effective'"
    ).fetchone()[0] == 1

    analyzed = client.post("/api/business-diagnosis/analyze")
    assert analyzed.status_code == 201, analyzed.text
    diagnosis_id = analyzed.json()["report"]["id"]
    confirmed = client.post(f"/api/business-diagnosis/{diagnosis_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["report"]["status"] == "confirmed"

    async def no_ai_provider(*_args, **_kwargs):
        return {"status": "failed", "reason": "t8_e_mocked_provider", "items": []}
    monkeypatch.setattr(master_plan_orchestrator, "generate_subplan_drafts", no_ai_provider)

    master = client.post("/api/plans", json={
        "topic_count_per_platform": 1, "dimension_allocation": {},
        "enabled_platforms": ["xhs"], "content_format": "image_text",
        "per_platform": {"xhs": {"topic_count": 1, "publish_slots": ["20:00"]}},
        "cadence": {"round_interval_days": 1, "rounds_planned": 1},
        "change_note": "T8-E API happy path",
    })
    assert master.status_code == 200, master.text
    master_id = master.json()["draft"]["id"]
    approved_master = client.post(f"/api/plans/{master_id}/review", json={"action": "approve"})
    assert approved_master.status_code == 200, approved_master.text
    assert approved_master.json()["effective"] is True

    subplan = client.post("/api/subplans", json={
        "id": "t8-e-api-subplan", "master_plan_version_id": master_id,
        "platform": "xhs", "title": "T8-E 小红书平台子计划",
        "snapshot": {"title": "T8-E 小红书平台子计划", "platform": "xhs",
                     "objective": "完成图文内容生产"},
        "stages": [{
            "stage_key": "t8-e-stage-1", "stage_order": 1,
            "title": "图文内容生产", "target_count": 1,
            "strategy_goal": "验证生产任务完整归属",
            "execution_strategy": "使用已审核图文素材",
            "completion_criteria": "生产任务成功创建并保留版本快照",
            "dependencies": [],
        }],
    })
    assert subplan.status_code == 201, subplan.text
    subplan_id = subplan.json()["subplan"]["id"]
    subplan_version_id = subplan.json()["draft"]["id"]
    stage_id = subplan.json()["draft"]["stages"][0]["id"]
    goal = client.post(f"/api/subplans/{subplan_id}/goals", json={
        "version_id": subplan_version_id,
        "goal_type": "quantity",
        "title": "T8-E 图文内容产出",
        "target_count": 1,
        "acceptance_criteria": "创建一条完整归属的生产任务",
        "stage_id": stage_id,
        "requires_production": True,
    })
    assert goal.status_code == 201, goal.text
    goal_id = goal.json()["goal"]["id"]
    production_plan = client.put(
        f"/api/subplans/{subplan_id}/goals/{goal_id}/production-plan",
        json={
            "content_direction": "使用已授权图文素材",
            "suggested_count": 1,
            "execution_conditions": "素材授权和隐私检查通过",
            "acceptance_relation": "生产任务绑定当前子目标",
        },
    )
    assert production_plan.status_code == 200, production_plan.text
    approved_subplan = client.post(f"/api/subplans/{subplan_id}/review", json={
        "version_id": subplan_version_id, "action": "approve",
    })
    assert approved_subplan.status_code == 200, approved_subplan.text

    line = client.post(f"/api/subplans/{subplan_id}/production-line/draft", json={
        "definition": {
            "title": "T8-E 图文生产线", "description": "端到端验收",
            "execution_mode": "sequential",
            "stages": [{
                "stage_key": "t8-e-stage-1", "batch_size": 1,
                "production_goal": "创建可追溯图文生产任务",
                "instructions": "使用已授权图文素材", "cadence": "single",
            }],
        },
        "change_note": "T8-E API happy path",
    })
    assert line.status_code == 201, line.text
    line_version_id = line.json()["draft"]["id"]
    line_id = line.json()["draft"]["production_line_id"]
    approved_line = client.post(
        f"/api/production-lines/{line_id}/versions/{line_version_id}/review",
        json={"action": "approve"},
    )
    assert approved_line.status_code == 200, approved_line.text
    assert approved_line.json()["effective"] is True

    opened = client.post(f"/api/subplans/{subplan_id}/rounds", json={"topic_count": 1})
    assert opened.status_code == 201, opened.text
    round_id = opened.json()["round_id"]
    assert opened.json()["bound_subplan_version_id"] == subplan_version_id
    assert opened.json()["bound_production_line_version_id"] == line_version_id
    assert opened.json()["production_task_count"] == 0
    assert opened.json()["task_creation"] == "deferred_to_round_executor"
    assert client.get("/api/production-tasks", params={"round_id": round_id}).json()["total"] == 0

    # The durable round is then submitted to the same global queue used by the
    # production UI. Concrete topic/platform task rows are created only after the
    # executor has selected a topic; no ordinal-only placeholders are exposed.
    submitted = client.post(
        f"/api/rounds/{round_id}/run",
        json={"topic_count": 1, "platforms": ["xhs"]},
    )
    assert submitted.status_code == 202, submitted.text
    queued = queue.get(submitted.json()["task_id"])
    assert queued is not None and queued.status == "queued"
    assert queued.task_type == "round_run" and queued.aggregate_id == str(round_id)

    # Isolate the downstream task/asset API contract with a concrete executor-shaped
    # task. Actual executor task creation and worker execution are covered by T8-B.
    task = ProductionTaskStore(conn).create_task(
        round_id=round_id,
        master_plan_version_id=int(master_id),
        sub_plan_id=subplan_id,
        sub_plan_version_id=int(subplan_version_id),
        strategy_step_id="t8-e-stage-1",
        platform="xhs",
        task_ordinal=1,
        idempotency_key=f"round:{round_id}:step:t8-e-stage-1:topic:t8-e-topic:platform:xhs",
        input_snapshot={"topic": {"id": "t8-e-topic", "title": "T8-E 测试选题"}},
        production_line_version_id=int(line_version_id),
    )
    assert task["round_id"] == round_id
    assert task["master_plan_version_id"] == master_id
    assert task["sub_plan_id"] == subplan_id
    assert task["sub_plan_version_id"] == subplan_version_id
    assert task["strategy_step_id"] == "t8-e-stage-1"
    assert task["production_line_version_id"] == line_version_id

    attached = client.post(f"/api/production-tasks/{task['id']}/assets", json=[asset_id])
    assert attached.status_code == 201, attached.text
    snapshot = client.get(f"/api/production-tasks/{task['id']}/assets")
    assert snapshot.status_code == 200, snapshot.text
    usage = snapshot.json()["items"][0]
    assert usage["asset_version_no"] == reviewed_asset.json()["version_no"]
    assert usage["permission_snapshot"] == "allowed"
    assert usage["privacy_snapshot"] == "clear"

    diagnosis = client.get("/api/business-diagnosis")
    assert diagnosis.status_code == 200
    # The production line was added after diagnosis and is a genuine capacity
    # resource change; this correctly requires a new diagnosis for future plans.
    assert diagnosis.json()["report"]["status"] == "stale"
    assert diagnosis.json()["report"]["source_is_current"] is False
    assert diagnosis.json()["report"]["strategy_is_current"] is False
    subplan_read = client.get(f"/api/subplans/{subplan_id}")
    assert subplan_read.json()["subplan"]["master_plan_version_id"] == master_id
    assert subplan_read.json()["subplan"]["current_version_id"] == subplan_version_id
    line_read = client.get(f"/api/subplans/{subplan_id}/production-line")
    assert line_read.json()["production_line"]["current_version_id"] == line_version_id
    assert line_read.json()["production_line"]["sub_plan_version_id"] == subplan_version_id
