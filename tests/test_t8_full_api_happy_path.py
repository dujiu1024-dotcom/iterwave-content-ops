"""T8-E API acceptance: confirmed diagnosis to an attributed production task."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine


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
async def test_confirmed_diagnosis_to_production_task_api_happy_path(workflow_client, monkeypatch):
    from src.api import business_diagnosis
    from src.core import master_plan_orchestrator

    client, _conn = workflow_client
    monkeypatch.setattr(business_diagnosis, "generate_diagnosis", _fake_diagnosis)

    profile = client.put("/api/business-profile", json={
        "business_name": "T8-E API 验收业务", "industry": "测试行业",
        "business_model": "图文内容运营", "business_stage": "测试阶段",
        "operating_goal": "完成图文内容生产",
    })
    assert profile.status_code == 200, profile.text

    asset = client.post("/api/original-assets", json={
        "asset_type": "product_image", "title": "T8-E 已授权图文素材",
        "description": "完整 API 主路径验收", "source_kind": "manual",
        "metadata": {"fixture": "t8-e", "media_type": "image"},
    })
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["id"]
    reviewed_asset = client.post(f"/api/original-assets/{asset_id}/review", json={
        "decision": "confirmed", "review_note": "确认许可、隐私与质量",
        "quality": {
            "commercial_use_status": "allowed", "privacy_status": "clear",
            "readability_status": "readable", "completeness_status": "complete",
            "duplicate_status": "unique",
        },
    })
    assert reviewed_asset.status_code == 200, reviewed_asset.text

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

    tasks = client.get("/api/production-tasks", params={"round_id": round_id})
    assert tasks.status_code == 200, tasks.text
    items = tasks.json().get("items", [])
    assert items, "生产入口已开轮，但没有创建对应生产任务"
    task = items[0]
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
    assert diagnosis.json()["report"]["status"] == "confirmed"
    assert diagnosis.json()["report"]["source_is_current"] is True
    subplan_read = client.get(f"/api/subplans/{subplan_id}")
    assert subplan_read.json()["subplan"]["master_plan_version_id"] == master_id
    assert subplan_read.json()["subplan"]["current_version_id"] == subplan_version_id
    line_read = client.get(f"/api/subplans/{subplan_id}/production-line")
    assert line_read.json()["production_line"]["current_version_id"] == line_version_id
    assert line_read.json()["production_line"]["sub_plan_version_id"] == subplan_version_id
