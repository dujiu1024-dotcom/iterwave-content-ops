"""T8-E API acceptance: confirmed diagnosis to an attributed production task."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.adapters.base import Platform

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine
from src.core.task_queue import GlobalTaskQueue
from src.core.production_task_store import ProductionTaskStore
from src.core.round_executor import RoundExecutor
from src.core.task_worker import GlobalTaskWorker


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
async def test_confirmed_diagnosis_to_production_task_api_happy_path(workflow_client, cfg, monkeypatch, tmp_path):
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

    # The worker must freeze only approved, minimum-provenance asset fields.
    asset_link = client.post(f"/api/original-assets/{asset_id}/links", json={
        "entity_type": "plan", "entity_id": subplan_id,
        "relation_type": "reference_candidate",
    })
    assert asset_link.status_code == 201, asset_link.text
    conn.execute(
        "UPDATE sub_plan_stages SET effect_required=1,effect_status='pending' WHERE id=?",
        (stage_id,),
    )
    conn.commit()

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
        json={"topic_count": 1, "platforms": ["xhs"], "save_drafts": False},
    )
    assert submitted.status_code == 202, submitted.text
    queued = queue.get(submitted.json()["task_id"])
    assert queued is not None and queued.status == "queued"
    assert queued.task_type == "round_run" and queued.aggregate_id == str(round_id)

    # Execute the queued round with the real RoundExecutor/Worker. Only external
    # model and browser rendering boundaries are deterministic test doubles.
    from src.core import round_executor as executor_module, task_snapshot
    from src.core.guard.ultimate_goal_checker import CheckOutcome, CheckResult

    monkeypatch.setattr(task_snapshot, "validate_execution_snapshot", lambda *a, **k: None)
    worker_cfg = cfg.model_copy(update={
        "paths": cfg.paths.model_copy(update={
            "data_dir": str(tmp_path / "tenant" / "data"),
            "output_dir": str(tmp_path / "tenant" / "output"),
        }),
    })
    topic = SimpleNamespace(
        id="t8-e-api-topic", title="T8-E 全链路真实 Worker 选题",
        summary="从已确认诊断与生效计划生成图文内容",
        dimension="AI教育", source="test", date="2026-11-05",
    )
    scored = SimpleNamespace(
        topic=topic, total_score=91.0, score_breakdown={}, is_exploration=False,
    )

    class PassingGuard:
        def check_content(self, *_args, **_kwargs):
            return CheckOutcome(CheckResult.PASS, "deterministic test pass")

    def fake_generate_card_set(*, output_dir, **_kwargs):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        image = output / "t8-e-full-api-card.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"deterministic-image")
        return [str(image)], []

    monkeypatch.setattr(executor_module, "load_pool", lambda *_: ([topic], {}))
    monkeypatch.setattr(executor_module, "_llm_available", lambda _cfg: True)
    monkeypatch.setattr(
        executor_module, "generate_copy_content",
        lambda *_args, **_kwargs: (
            "T8-E 真实 Worker 图文标题",
            "这条图文内容由真实 RoundExecutor 生成，用于验证诊断到反馈的完整业务链路。",
            ["AI教育"],
        ),
    )
    monkeypatch.setattr(
        executor_module.PlanVersionManager, "load_plan",
        lambda _self: (
            {"enabled_platforms": ["xhs"], "topic_count_per_platform": 1,
             "dimension_allocation": {}},
            "t8-e-frozen-plan-signature", None, "test",
        ),
    )
    monkeypatch.setattr(executor_module, "effective_persona", lambda *_: {"persona_keywords": ["AI教育"]})
    monkeypatch.setattr(RoundExecutor, "_select_topics", lambda self, *_: [scored])
    monkeypatch.setattr(RoundExecutor, "_resolve_platforms", lambda self, *_: [Platform.XHS])
    monkeypatch.setattr(RoundExecutor, "_record_plan_signature", lambda *a, **k: None)
    monkeypatch.setattr(RoundExecutor, "_platform_broken", lambda *a, **k: False)
    monkeypatch.setattr(RoundExecutor, "_image_settings", lambda self: (
        {"model": "deterministic-image", "fallback_enabled": False}, "",
    ))
    monkeypatch.setattr(
        "src.core.ai_card_generation.generate_ai_card_set", fake_generate_card_set,
    )
    monkeypatch.setattr(
        "src.core.visual_ocr.load_ocr_report",
        lambda _paths: {"status": "passed", "gate": "passed", "reason": None},
    )
    monkeypatch.setattr(
        "src.core.visual_review.assess_visual_pages",
        lambda _paths: {"status": "not_evaluated", "reason": "deterministic test", "score": None},
    )

    def build_tenant_context(tenant_id):
        assert tenant_id == "default"
        from src.core.db import Database
        worker_conn = Database(worker_cfg).connect()
        executor = RoundExecutor(
            worker_cfg, worker_conn, scoring=None, guard=PassingGuard(), adapters={},
            sm_round=RoundStateMachine(worker_conn, cfg=worker_cfg),
            sm_draft=DraftStateMachine(worker_conn, cfg=worker_cfg),
        )
        return {
            "cfg": worker_cfg, "conn": worker_conn, "executor": executor,
            "scheduler": SimpleNamespace(shutdown=lambda: None), "notifier": None,
        }

    stop = asyncio.Event()

    class StopAfterOneWorker(GlobalTaskWorker):
        async def _execute(self, task, **kwargs):
            await super()._execute(task, **kwargs)
            stop.set()

    worker = StopAfterOneWorker(
        queue, build_tenant_context, poll_interval_seconds=0.01,
        lease_seconds=30, heartbeat_seconds=1, reconcile_terminal_rounds=False,
    )
    await worker.run(stop)

    queue_task_id = submitted.json()["task_id"]
    completed = queue.get(queue_task_id)
    assert completed is not None and completed.status == "succeeded"
    tasks = ProductionTaskStore(conn).list_tasks(round_id=round_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["status"] == "succeeded"
    assert task["content_id"]
    assert task["master_plan_version_id"] == master_id
    assert task["sub_plan_id"] == subplan_id
    assert task["sub_plan_version_id"] == subplan_version_id
    assert task["strategy_step_id"] == "t8-e-stage-1"
    assert task["production_line_version_id"] == line_version_id

    frozen_assets = task["input_snapshot"]["task_input"]["original_assets"]
    assert len(frozen_assets) == 1
    assert frozen_assets[0]["asset_id"] == asset_id
    assert frozen_assets[0]["version_no"] == reviewed_asset.json()["version_no"]
    assert frozen_assets[0]["privacy_status"] == "clear"
    assert "metadata" not in frozen_assets[0]
    assert "fixture" not in json.dumps(task["input_snapshot"], ensure_ascii=False)
    # A reference candidate is not falsely counted as consumed by the generator.
    assert conn.execute(
        "SELECT COUNT(*) FROM production_task_asset_usages WHERE production_task_id=?",
        (task["id"],),
    ).fetchone()[0] == 0

    content = conn.execute(
        "SELECT id,production_task_id,round_id,title FROM contents WHERE production_task_id=?",
        (task["id"],),
    ).fetchone()
    assert content is not None and content["id"] == task["content_id"]
    assert content["title"] == "T8-E 真实 Worker 图文标题"
    draft = conn.execute(
        "SELECT id,status FROM drafts WHERE content_id=?", (content["id"],),
    ).fetchone()
    assert draft is not None and draft["status"] == "draft_pending"

    rejected_content = client.post(
        f"/api/contents/{content['id']}/reject",
        json={"reason": "首次审核要求补充证据"},
    )
    assert rejected_content.status_code == 200, rejected_content.text
    resubmitted_content = client.post(
        f"/api/contents/{content['id']}/resubmit",
        json={"reason": "已补充证据并重新提交"},
    )
    assert resubmitted_content.status_code == 200, resubmitted_content.text

    approved_content = client.post(
        f"/api/contents/{content['id']}/approve",
        json={"reason": "T9-C 真实 Worker 成品审核通过"},
    )
    assert approved_content.status_code == 200, approved_content.text
    assert approved_content.json()["review_status"] == "approved"

    published = client.post(
        f"/api/drafts/{draft['id']}/publish_confirm",
        json={"note_url": "https://www.xiaohongshu.com/explore/t8-e-full-api"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "user_published"

    published_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    verification = client.post(
        f"/api/drafts/{draft['id']}/publish-verification",
        json={
            "platform_content_id": "t8-e-full-api",
            "content_url": "https://www.xiaohongshu.com/explore/t8-e-full-api",
            "published_at": published_at,
            "idempotency_key": "t8-e-full-api-publish-verification",
        },
    )
    assert verification.status_code == 201, verification.text
    verification_id = verification.json()["verification"]["verification_id"]
    assert verification.json()["closure_event_pending"] is True

    claimed = client.post("/api/production-line-close-events/claim", json={
        "worker_id": "t8-e-e2e-outbox-consumer", "limit": 10,
    })
    assert claimed.status_code == 200, claimed.text
    events = claimed.json()["items"]
    assert len(events) == 1
    event_id = events[0]["event_id"]
    acknowledged = client.post(
        f"/api/production-line-close-events/{event_id}/ack",
        json={"succeeded": True},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "delivered"
    verification_row = conn.execute(
        "SELECT closure_status FROM content_publish_verifications WHERE verification_id=?",
        (verification_id,),
    ).fetchone()
    assert verification_row["closure_status"] == "succeeded"
    line_row = conn.execute(
        "SELECT status FROM content_production_lines WHERE production_task_id=?",
        (task["id"],),
    ).fetchone()
    assert line_row["status"] == "published"

    metrics = client.post(
        f"/api/drafts/{draft['id']}/metrics/manual",
        json={"views": 120, "likes": 12, "collects": 8, "comments": 3},
        headers={"Idempotency-Key": "t8-e-full-api-metrics"},
    )
    assert metrics.status_code == 200, metrics.text
    metric_snapshot = client.get(
        "/api/metrics/snapshots", params={"draft_id": draft["id"]},
    )
    assert metric_snapshot.status_code == 200
    assert metric_snapshot.json()["total"] == 1
    assert metric_snapshot.json()["items"][0]["content_id"] == content["id"]

    feedback = client.post(
        f"/api/subplans/stages/{stage_id}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "保留当前内容方向并继续扩充样本。",
            "evidence_note": "同一条真实 Worker 成品经审核、发布核验、Outbox 消费和指标回填。",
            "confidence": "medium",
        },
    )
    assert feedback.status_code == 201, feedback.text
    effect_decision = client.post(
        f"/api/subplans/{subplan_id}/stages/{stage_id}/condition-review",
        json={
            "condition_type": "effect", "decision": "passed",
            "reason": "确认真实发布成品的效果证据",
            "effect_review_id": feedback.json()["id"],
        },
    )
    assert effect_decision.status_code == 200, effect_decision.text
    assert effect_decision.json()["stage"]["effect_status"] == "passed"

    adopted = client.post("/api/plan_adjustments/adopt", json={
        "dimensions": ["内容结构"], "effect_review_id": feedback.json()["id"],
    })
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["effective"] is False
    assert adopted.json()["requires_human_review"] is True
    assert len(adopted.json()["drafts"]) == 1
    adopted_snapshot = json.loads(adopted.json()["drafts"][0]["draft_version"]["snapshot_json"])
    provenance = adopted_snapshot["review_adoptions"][-1]["effect_feedback"]
    assert provenance["id"] == feedback.json()["id"]
    assert provenance["stage_id"] == stage_id
    assert provenance["evidence"]["manual_metrics"]["views"] == 120.0

    adopted_version_id = adopted.json()["drafts"][0]["draft_version"]["id"]
    approved_adoption = client.post(
        f"/api/subplans/{subplan_id}/review",
        json={"version_id": adopted_version_id, "action": "approve"},
    )
    assert approved_adoption.status_code == 200, approved_adoption.text
    assert approved_adoption.json()["effective"] is True

    adopted_stage = conn.execute(
        "SELECT stage_key FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order LIMIT 1",
        (adopted_version_id,),
    ).fetchone()
    assert adopted_stage is not None
    next_line = client.post(f"/api/subplans/{subplan_id}/production-line/draft", json={
        "definition": {
            "title": "T8-E 反馈采纳后的生产线",
            "description": "绑定正式审核后的新子计划版本",
            "execution_mode": "sequential",
            "stages": [{
                "stage_key": adopted_stage["stage_key"], "batch_size": 1,
                "production_goal": "按反馈采纳后的策略生产内容",
                "instructions": "遵循新版本的已审核策略",
            }],
        },
        "change_note": "同步已审核采纳的策略版本",
    })
    assert next_line.status_code == 201, next_line.text
    next_line_version_id = next_line.json()["draft"]["id"]
    next_line_review = client.post(
        f"/api/production-lines/{line_id}/versions/{next_line_version_id}/review",
        json={"action": "approve"},
    )
    assert next_line_review.status_code == 200, next_line_review.text
    assert next_line_review.json()["effective"] is True

    diagnosis = client.get("/api/business-diagnosis")
    assert diagnosis.status_code == 200
    # The production line was added after diagnosis and is a genuine capacity
    # resource change; this correctly requires a new diagnosis for future plans.
    assert diagnosis.json()["report"]["status"] == "stale"
    assert diagnosis.json()["report"]["source_is_current"] is False
    assert diagnosis.json()["report"]["strategy_is_current"] is False
    subplan_read = client.get(f"/api/subplans/{subplan_id}")
    assert subplan_read.json()["subplan"]["master_plan_version_id"] == master_id
    assert subplan_read.json()["subplan"]["current_version_id"] == adopted_version_id
    line_read = client.get(f"/api/subplans/{subplan_id}/production-line")
    assert line_read.json()["production_line"]["current_version_id"] == next_line_version_id
    assert line_read.json()["production_line"]["sub_plan_version_id"] == adopted_version_id
