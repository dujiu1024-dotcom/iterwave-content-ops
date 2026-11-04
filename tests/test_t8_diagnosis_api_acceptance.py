"""T8-A API-level acceptance for diagnosis lifecycle and failure atomicity."""

from __future__ import annotations

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


def test_master_plan_api_blocks_unconfirmed_and_stale_diagnosis(diagnosis_client, monkeypatch):
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
    assert "确认 AI 经营分析报告" in blocked.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0] == 0

    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis",
        _async_fake_diagnosis,
    )
    analyzed = client.post("/api/business-diagnosis/analyze")
    report_id = analyzed.json()["report"]["id"]
    assert client.post(f"/api/business-diagnosis/{report_id}/confirm").status_code == 200

    changed = client.put(
        "/api/business-profile",
        json={"business_name": "过期诊断测试", "change_note": "invalidate diagnosis"},
    )
    assert changed.status_code == 200
    stale = client.post("/api/plans", json=payload)
    assert stale.status_code == 409
    assert "已变化" in stale.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM business_diagnosis_reports WHERE id=?", (report_id,)
    ).fetchone()["status"] == "stale"


def _create_provenance_valid_task(conn):
    """Build a real, fully attributed plan -> line -> round -> task chain."""
    import json
    from datetime import datetime, timezone

    from src.core.production_line_store import ProductionLineStore
    from src.core.production_task_store import ProductionTaskStore

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
    conn.execute(
        "UPDATE master_plan_versions SET status='effective',effective_at=? WHERE id=?",
        (now, master_version_id),
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
    conn.execute(
        "UPDATE sub_plan_versions SET status='effective',effective_at=? WHERE id=?",
        (now, sub_version_id),
    )
    conn.execute(
        "UPDATE sub_plans SET status='active',current_version_id=?,updated_at=? WHERE id=?",
        (sub_version_id, now, sub_plan_id),
    )
    stage_cursor = conn.execute(
        """INSERT INTO sub_plan_stages(
            sub_plan_version_id,stage_order,title,target_count,status,created_at,updated_at,
            stage_key,strategy_goal,execution_strategy
        ) VALUES(?,1,'授权验收阶段',1,'pending',?,?, 't8-a-stage','验证素材授权门禁','测试')""",
        (sub_version_id, now, now),
    )
    stage_id = int(stage_cursor.lastrowid)
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


def test_original_asset_permission_and_privacy_gate_production_usage(diagnosis_client):
    client, conn = diagnosis_client
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

    reviewed = client.post(f"/api/original-assets/{asset_id}/review", json={
        "decision": "confirmed",
        "review_note": "人工确认许可与隐私状态",
        "quality": {
            "commercial_use_status": "allowed",
            "privacy_status": "clear",
            "readability_status": "readable",
            "completeness_status": "complete",
            "duplicate_status": "unique",
        },
    })
    assert reviewed.status_code == 200
    allowed = client.post(f"/api/production-tasks/{task_id}/assets", json=[asset_id])
    assert allowed.status_code == 201
    assert allowed.json()["assets"][0]["permission_snapshot"] == "allowed"
    snapshot = client.get(f"/api/production-tasks/{task_id}/assets")
    assert snapshot.status_code == 200
    assert snapshot.json()["items"][0]["asset_version_no"] == reviewed.json()["version_no"]
