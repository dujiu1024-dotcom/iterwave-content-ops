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


def test_diagnosis_api_requires_fresh_report_confirmation(diagnosis_client, monkeypatch):
    from src.api import business_diagnosis

    client, conn = diagnosis_client
    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis",
        lambda _conn, _cfg: _fake_diagnosis(_conn, _cfg),
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
        lambda _conn, _cfg: _fake_diagnosis(_conn, _cfg),
    )
    first = client.post("/api/business-diagnosis/analyze")
    assert first.status_code == 201
    report_id = first.json()["report"]["id"]
    assert client.post(f"/api/business-diagnosis/{report_id}/confirm").status_code == 200

    before = conn.execute(
        "SELECT id,version_no,status FROM business_diagnosis_reports ORDER BY version_no"
    ).fetchall()
    monkeypatch.setattr(
        business_diagnosis, "generate_diagnosis",
        lambda _conn, _cfg: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )
    failed = client.post("/api/business-diagnosis/analyze")
    assert failed.status_code == 502

    after = conn.execute(
        "SELECT id,version_no,status FROM business_diagnosis_reports ORDER BY version_no"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert after[0]["status"] == "confirmed"
