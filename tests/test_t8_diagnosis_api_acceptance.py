"""T8-A API-level acceptance for diagnosis lifecycle and failure atomicity."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine


@pytest.fixture()
def diagnosis_client(cfg, conn, test_auth):
    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "cfg": cfg,
        "conn": conn,
        "sm_draft": DraftStateMachine(conn),
        "sm_round": RoundStateMachine(conn),
        "plan_version_manager": PlanVersionManager(cfg, conn),
        "normalizer": Normalizer(conn, cfg),
        "notifier": Notifier(cfg, conn),
    })
    app = api_main._build_app()
    try:
        yield test_auth(TestClient(app, base_url="http://127.0.0.1")), conn
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


def _fake_diagnosis(conn, cfg):
    from src.core.business_diagnosis import collect_business_context

    context = collect_business_context(conn, cfg)
    return (
        {
            "business_summary": {"claim": "测试业务", "classification": "fact"},
            "assumptions": [],
            "source_references": [],
        },
        context["context"],
        "t8-test-model",
    )


async def _async_fake_diagnosis(conn, cfg):
    return _fake_diagnosis(conn, cfg)


def test_diagnosis_api_requires_fresh_report_confirmation(diagnosis_client, monkeypatch):
    from src.api import business_diagnosis

    client, conn = diagnosis_client
    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis",
        _async_fake_diagnosis,
    )

    analyzed = client.post("/api/business-diagnosis/analyze")
    assert analyzed.status_code == 201
    report = analyzed.json()["report"]
    report_id = report["id"]
    assert report["status"] == "draft"
    assert report["source_is_current"] is True

    confirmed = client.post(f"/api/business-diagnosis/{report_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["report"]["status"] == "confirmed"

    # A material business-profile change invalidates the report's source fingerprint.
    updated = client.put(
        "/api/business-profile",
        json={"business_name": "变更后的业务", "change_note": "T8-A stale-report test"},
    )
    assert updated.status_code == 200

    stale_confirm = client.post(f"/api/business-diagnosis/{report_id}/confirm")
    assert stale_confirm.status_code == 409
    assert "已变化" in stale_confirm.json()["detail"]
    saved = conn.execute(
        "SELECT status FROM business_diagnosis_reports WHERE id=?", (report_id,)
    ).fetchone()
    assert saved["status"] == "stale"


def test_diagnosis_model_failure_does_not_supersede_existing_report(diagnosis_client, monkeypatch):
    from src.api import business_diagnosis

    client, conn = diagnosis_client
    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis",
        _async_fake_diagnosis,
    )
    first = client.post("/api/business-diagnosis/analyze")
    assert first.status_code == 201
    report_id = first.json()["report"]["id"]
    assert client.post(f"/api/business-diagnosis/{report_id}/confirm").status_code == 200

    before = conn.execute(
        "SELECT id,version_no,status FROM business_diagnosis_reports ORDER BY version_no"
    ).fetchall()
    async def _async_model_failure(_conn, _cfg):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis",
        _async_model_failure,
    )
    failed = client.post("/api/business-diagnosis/analyze")
    assert failed.status_code == 502

    after = conn.execute(
        "SELECT id,version_no,status FROM business_diagnosis_reports ORDER BY version_no"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert after[0]["status"] == "confirmed"


def test_manual_master_plan_fallback_needs_north_star_but_not_ai_diagnosis(diagnosis_client, monkeypatch):
    from src.api import business_diagnosis

    client, conn = diagnosis_client
    payload = {
        "topic_count_per_platform": 5,
        "dimension_allocation": {},
        "enabled_platforms": ["xhs"],
        "content_format": "image_text",
    }
    blocked = client.post("/api/plans", json=payload)
    assert blocked.status_code == 409
    assert "账号组北极星" in blocked.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0] == 0

    north_star = client.put("/api/north-star", json={
        "change_reason": "验证无模型人工创建门禁",
        "snapshot": {
            "positioning": "专业知识型个人IP",
            "business_direction": "围绕专业服务建立信任",
            "target_users": "有明确需求的目标客户",
            "core_value": "提供可验证的专业解决方案",
            "long_term_content_direction": "案例、方法论和实操经验",
            "business_model": "服务型业务",
            "platform_relationships": "小红书获客，公众号沉淀",
            "long_term_goals": "建立稳定的专业服务品牌",
        },
    })
    assert north_star.status_code == 200, north_star.text

    # Manual drafting is a real no-model fallback and does not require an AI diagnosis.
    manual = client.post("/api/plans", json=payload)
    assert manual.status_code == 200, manual.text
    assert manual.json()["draft"]["source"] == "user"
    assert manual.json()["effective"] is False

    # AI generation remains diagnosis-gated and refuses a strategic source change.
    no_diagnosis = client.post("/api/plans/ai-draft")
    assert no_diagnosis.status_code == 409
    assert "经营诊断报告" in no_diagnosis.json()["detail"]

    monkeypatch.setattr(business_diagnosis, "generate_diagnosis", _async_fake_diagnosis)
    analyzed = client.post("/api/business-diagnosis/analyze")
    report_id = analyzed.json()["report"]["id"]
    assert client.post(f"/api/business-diagnosis/{report_id}/confirm").status_code == 200
    changed = client.put(
        "/api/business-profile",
        json={"business_name": "战略资料已变化", "change_note": "invalidate AI diagnosis"},
    )
    assert changed.status_code == 200
    stale = client.post("/api/plans/ai-draft")
    assert stale.status_code == 409
    assert "北极星或资源基线已变化" in stale.json()["detail"]
    assert conn.execute(
        "SELECT status FROM business_diagnosis_reports WHERE id=?", (report_id,)
    ).fetchone()["status"] == "confirmed"


def _create_provenance_valid_task(conn):
    """Build a real, fully attributed plan -> line -> round -> task chain."""
    import json
    from datetime import datetime, timezone

    from src.core.production_line_store import ProductionLineStore
    from src.core.production_task_store import ProductionTaskStore
    from src.core.subgoal_store import SubGoalStore

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan_version_no = int(conn.execute(
        "SELECT COALESCE(MAX(version_no),0)+1 FROM master_plan_versions"
    ).fetchone()[0])
    plan_cursor = conn.execute(
        """INSERT INTO master_plan_versions(
            version_no,plan_signature,plan_snapshot,status,source,created_at,created_by
        ) VALUES(?,?,?,'draft','system',?,'t8-a-test')""",
        (plan_version_no, f"t8-a-{plan_version_no}", json.dumps({"fixture":"t8-a"}),
         now),
    )
    master_version_id = int(plan_cursor.lastrowid)
    north_star = conn.execute(
        "SELECT id,snapshot_json FROM account_group_north_star_versions "
        "WHERE status='effective' ORDER BY version_no DESC LIMIT 1"
    ).fetchone()
    if north_star is None:
        raise AssertionError("测试必须先建立有效北极星版本")
    conn.execute(
        """UPDATE master_plan_versions
           SET north_star_version_id=?,north_star_snapshot_json=?,
               north_star_attribution_status='attributed',
               status='effective',effective_at=?
           WHERE id=?""",
        (int(north_star["id"]), north_star["snapshot_json"], now, master_version_id),
    )

    sub_plan_id = f"t8-a-subplan-{master_version_id}"
    sub_plan_cursor = conn.execute(
        """INSERT INTO sub_plans(
            id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
        ) VALUES(?,?, 'xhs','T8-A 授权验收子计划','draft',?,?, 't8-a-test')""",
        (sub_plan_id, master_version_id, now, now),
    )
    sub_version_cursor = conn.execute(
        """INSERT INTO sub_plan_versions(
            sub_plan_id,version_no,master_plan_version_id,snapshot_json,status,created_at,created_by
        ) VALUES(?,1,?,?,'draft',?,'t8-a-test')""",
        (sub_plan_id, master_version_id, json.dumps({"fixture":"t8-a"}), now),
    )
    sub_version_id = int(sub_version_cursor.lastrowid)
    stage_cursor = conn.execute(
        """INSERT INTO sub_plan_stages(
            sub_plan_version_id,stage_order,title,target_count,status,created_at,updated_at,
            stage_key,strategy_goal,execution_strategy
        ) VALUES(?,1,'授权验收阶段',1,'pending',?,?, 't8-a-stage','验证素材授权门禁','测试')""",
        (sub_version_id, now, now),
    )
    stage_id = int(stage_cursor.lastrowid)
    conn.commit()

    subgoals = SubGoalStore(conn)
    goal = subgoals.create_goal(sub_plan_id, sub_version_id, {
        "goal_type": "quantity",
        "title": "授权素材图文生产",
        "target_count": 1,
        "acceptance_criteria": "生成一条具备素材授权快照的生产任务",
        "stage_id": stage_id,
        "requires_production": True,
    })
    subgoals.save_production_plan(sub_plan_id, goal["id"], {
        "content_direction": "使用已确认授权的素材生成图文内容",
        "suggested_count": 1,
        "execution_conditions": "素材权限与隐私状态均通过校验",
        "acceptance_relation": "生产任务必须绑定本子目标和策略阶段",
    })
    conn.execute(
        "UPDATE sub_plan_versions SET status='effective',effective_at=? WHERE id=?",
        (now, sub_version_id),
    )
    conn.execute(
        "UPDATE sub_plans SET status='active',current_version_id=?,updated_at=? WHERE id=?",
        (sub_version_id, now, sub_plan_id),
    )
    conn.commit()

    line_store = ProductionLineStore(conn)
    line_draft = line_store.create_draft(
        sub_plan_id, sub_version_id,
        {
            "title": "T8-A 验收生产线",
            "description": "仅用于端到端授权门禁验收",
            "stages": [{
                "stage_key": "t8-a-stage",
                "batch_size": 1,
                "production_goal": "验证素材授权门禁",
            }],
        },
        generation_source="system",
        author="t8-a-test",
    )
    line_store.activate(int(line_draft["id"]), reviewer="t8-a-test")
    line_version_id = int(line_draft["id"])

    round_no = int(conn.execute(
        "SELECT COALESCE(MAX(round_number),0)+1 FROM spiral_rounds"
    ).fetchone()[0])
    round_cursor = conn.execute(
        """INSERT INTO spiral_rounds(
            round_number,status,topic_ids,content_variants,master_plan_version_id,
            sub_plan_id,sub_plan_version_id,sub_plan_stage_id,sub_plan_snapshot,
            sub_plan_stage_snapshot,plan_attribution_status,production_line_version_id
        ) VALUES(?,'created','[]','[]',?,?,?,?,?,?, 'resolved',?)""",
        (
            round_no, master_version_id, sub_plan_id, sub_version_id, stage_id,
            json.dumps({"sub_plan_id":sub_plan_id,"version_id":sub_version_id}),
            json.dumps({"stage_id":stage_id,"stage_key":"t8-a-stage"}),
            line_version_id,
        ),
    )
    round_id = int(round_cursor.lastrowid)
    conn.commit()

    task = ProductionTaskStore(conn).create_task(
        round_id=round_id,
        master_plan_version_id=master_version_id,
        sub_plan_id=sub_plan_id,
        sub_plan_version_id=sub_version_id,
        strategy_step_id="t8-a-stage",
        platform="xhs",
        task_ordinal=1,
        idempotency_key="t8-a-asset-permission-gate",
        input_snapshot={"acceptance":"T8-A original asset permission gate"},
    )
    return str(task["id"])


def test_original_asset_permission_and_privacy_gate_production_usage(diagnosis_client, monkeypatch):
    from src.api import business_diagnosis

    client, conn = diagnosis_client

    profile = client.put("/api/business-profile", json={
        "business_name": "T8-A 授权门禁测试业务",
        "introduction": "用于验证原始素材权限与隐私门禁的图文内容运营业务",
        "industry": "内容运营",
        "business_model": "服务",
        "business_stage": "测试验收",
        "operating_goal": "验证生产上下文完整性",
        "change_note": "T8-A provenance fixture",
    })
    assert profile.status_code == 200, profile.text
    offering = client.post("/api/business-profile/items/service", json={
        "name": "T8-A 图文内容运营服务",
        "details": {
            "service_type": "图文内容运营",
            "introduction": "用于素材权限门禁验收",
            "delivery_method": "线上内容策划与制作",
        },
    })
    assert offering.status_code == 200, offering.text

    north_star = client.put("/api/north-star", json={
        "positioning": "以可验证的内容运营服务支持业务增长",
        "business_direction": "持续建设合规图文内容生产能力",
        "target_users": "需要图文内容运营的企业客户",
        "core_value": "可追溯、合规、稳定的内容生产",
        "long_term_content_direction": "围绕真实业务资料生产高质量图文内容",
        "business_model": "企业内容运营服务",
        "long_term_goals": "建立可审计的图文生产闭环",
        "change_reason": "T8-A 生产上下文验收测试",
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

    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis", _async_fake_diagnosis,
    )
    analyzed = client.post("/api/business-diagnosis/analyze")
    assert analyzed.status_code == 201, analyzed.text
    report_id = analyzed.json()["report"]["id"]
    confirmed = client.post(f"/api/business-diagnosis/{report_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    cur = conn.execute(
        """INSERT INTO original_assets(
            asset_type,title,description,source_kind,metadata_json
        ) VALUES('product_image','T8-A 授权门禁测试素材','测试素材','manual','{"fixture":"t8-a"}')"""
    )
    asset_id = cur.lastrowid
    conn.execute(
        """INSERT INTO original_asset_versions(
            original_asset_id,version_no,title,description,metadata_json,
            review_status,commercial_use_status
        ) VALUES(?,1,'T8-A 授权门禁测试素材','测试素材','{"fixture":"t8-a"}','pending','unknown')""",
        (asset_id,),
    )
    conn.commit()
    # Create the task through the production store with the complete immutable
    # plan/subplan/stage/line/round provenance required by database triggers.
    task_id = _create_provenance_valid_task(conn)

    # Pending/unknown permission and privacy must never enter a task snapshot.
    denied = client.post(f"/api/production-tasks/{task_id}/assets", json=[asset_id])
    assert denied.status_code == 409
    assert conn.execute(
        "SELECT COUNT(*) FROM production_task_asset_usages WHERE production_task_id=?",
        (task_id,),
    ).fetchone()[0] == 0

    review_payload = {
        "decision": "confirmed",
        "review_note": "人工确认许可与隐私状态",
        "quality": {
            "commercial_use_status": "allowed",
            "privacy_status": "clear",
            "readability_status": "readable",
            "completeness_status": "complete",
            "duplicate_status": "unique",
        },
    }
    reviewed = client.post(
        f"/api/original-assets/{asset_id}/review",
        content=json.dumps(review_payload, ensure_ascii=False),
        headers={"content-type": "application/json"},
    )
    assert reviewed.status_code == 200, (
        f"{reviewed.text}; sent={reviewed.request.content!r}"
    )
    allowed = client.post(f"/api/production-tasks/{task_id}/assets", json=[asset_id])
    assert allowed.status_code == 201
    assert allowed.json()["assets"][0]["permission_snapshot"] == "allowed"
    snapshot = client.get(f"/api/production-tasks/{task_id}/assets")
    assert snapshot.status_code == 200
    assert snapshot.json()["items"][0]["asset_version_no"] == reviewed.json()["version_no"]



def test_business_summary_includes_manual_metric_facts(diagnosis_client, seed):
    """经营汇总必须合并人工回填指标，而不是只读取自动采集 metrics。"""
    client, conn = diagnosis_client
    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=CURRENT_TIMESTAMP,published_at=CURRENT_TIMESTAMP WHERE id=?",
        (draft_id,),
    )
    conn.commit()

    submitted = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json={"views": 1234, "likes": 87},
        headers={"Idempotency-Key": "business-summary-manual-facts"},
    )
    assert submitted.status_code == 200, submitted.text

    summary = client.get("/api/analytics/business-summary?days=30")
    assert summary.status_code == 200, summary.text
    interactions = summary.json()["interactions"]
    assert interactions["views"] == 1234
    assert interactions["likes"] == 87
    assert interactions["views_samples"] == 1
    assert interactions["likes_samples"] == 1
