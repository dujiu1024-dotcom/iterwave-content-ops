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


def test_original_asset_permission_and_privacy_gate_production_usage(diagnosis_client):
    client, conn = diagnosis_client
    created = client.post("/api/original-assets", json={
        "asset_type": "product_image",
        "title": "T8-A 授权门禁测试素材",
        "description": "测试素材",
        "source_kind": "manual",
        "metadata": {"fixture": "t8-a"},
    })
    assert created.status_code == 201, created.text
    asset_id = created.json()["id"]
    conn.execute("INSERT INTO production_tasks(id) VALUES('t8-a-asset-task')")
    conn.commit()

    # Pending/unknown permission and privacy must never enter a task snapshot.
    denied = client.post("/api/production-tasks/t8-a-asset-task/assets", json=[asset_id])
    assert denied.status_code == 409
    assert conn.execute(
        "SELECT COUNT(*) FROM production_task_asset_usages WHERE production_task_id='t8-a-asset-task'"
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
    allowed = client.post("/api/production-tasks/t8-a-asset-task/assets", json=[asset_id])
    assert allowed.status_code == 201
    assert allowed.json()["assets"][0]["permission_snapshot"] == "allowed"
    snapshot = client.get("/api/production-tasks/t8-a-asset-task/assets")
    assert snapshot.status_code == 200
    assert snapshot.json()["items"][0]["asset_version_no"] == reviewed.json()["version_no"]
