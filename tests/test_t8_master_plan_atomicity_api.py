"""T8-D API acceptance: AI master-plan failure atomicity and concurrent review conflicts."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.plan_version_store import PlanVersionStore
from src.core.state_machine import DraftStateMachine, RoundStateMachine


@pytest.fixture()
def master_plan_client(cfg, conn, test_auth):
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
        "runtime_tenant_id": "default",
    })
    app = api_main._build_app()
    try:
        yield test_auth(TestClient(app, base_url="http://127.0.0.1")), conn, cfg
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
        "t8-d-test-model",
    )


async def _async_fake_diagnosis(conn, cfg):
    return _fake_diagnosis(conn, cfg)


def _confirm_diagnosis(client, monkeypatch, conn):
    from src.api import business_diagnosis

    if conn.execute("SELECT 1 FROM account_group_north_star_versions WHERE status='effective' LIMIT 1").fetchone() is None:
        conn.execute(
            """INSERT INTO account_group_north_star_versions(
                version_no,snapshot_json,status,change_reason,created_by,created_at,effective_at
            ) VALUES(1,?,'effective','T8 AI generation prerequisite','t8-test','now','now')""",
            (json.dumps({
                "positioning": "T8 测试定位", "business_direction": "T8 测试方向",
                "target_users": "T8 测试用户", "core_value": "T8 测试价值",
                "long_term_content_direction": "T8 测试内容方向", "business_model": "T8 测试模式",
                "platform_relationships": "", "long_term_goals": "T8 测试目标",
            }, ensure_ascii=False),),
        )
        conn.commit()
    monkeypatch.setattr(business_diagnosis, "generate_diagnosis", _async_fake_diagnosis)
    analyzed = client.post("/api/business-diagnosis/analyze")
    assert analyzed.status_code == 201, analyzed.text
    report_id = analyzed.json()["report"]["id"]
    confirmed = client.post(f"/api/business-diagnosis/{report_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text


def _plan(count=5):
    return {
        "topic_count_per_platform": count,
        "dimension_allocation": {"产品体验": count},
        "enabled_platforms": ["xhs"],
        "content_format": "image_text",
    }


def _seed_effective_plan_graph(conn):
    """Create a real effective master + child plan + active production-line graph."""
    from src.core.production_line_store import ProductionLineStore

    store = PlanVersionStore(conn)
    master = store.create_master_draft(_plan(), author="t8-d-test", source="system")
    store.activate_master_version(int(master["id"]), reviewer="t8-d-test")
    store.create_subplan(
        "t8-d-existing-subplan",
        master_plan_version_id=int(master["id"]),
        platform="xhs",
        title="T8-D 既有子计划",
        author="t8-d-test",
    )
    sub_version = store.create_subplan_version(
        "t8-d-existing-subplan",
        {"title": "T8-D 既有子计划", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[{"stage_key": "existing", "title": "既有阶段", "target_count": 5}],
        author="t8-d-test",
    )
    store.activate_subplan_version(int(sub_version["id"]), reviewer="t8-d-test")
    stage = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (int(sub_version["id"]),),
    ).fetchone()
    line_store = ProductionLineStore(conn)
    line_draft = line_store.create_draft(
        "t8-d-existing-subplan",
        int(sub_version["id"]),
        {"title": "T8-D 既有生产线", "stages": [{
            "stage_key": str(stage["stage_key"]),
            "batch_size": int(stage["target_count"]),
            "production_goal": str(stage["title"]),
        }]},
        generation_source="manual",
        author="t8-d-test",
    )
    line_store.activate(int(line_draft["id"]), reviewer="t8-d-test")
    return int(master["id"])


def _plan_graph_snapshot(conn):
    def rows(sql):
        return [tuple(row) for row in conn.execute(sql).fetchall()]

    return {
        "masters": rows(
            "SELECT id,version_no,status,plan_snapshot,plan_signature,based_on_version_id "
            "FROM master_plan_versions ORDER BY id"
        ),
        "settings": rows(
            "SELECT id,plan_snapshot,plan_signature,active_version_id FROM plan_settings ORDER BY id"
        ),
        "subplans": rows(
            "SELECT id,master_plan_version_id,status,current_version_id FROM sub_plans ORDER BY id"
        ),
        "subplan_versions": rows(
            "SELECT id,sub_plan_id,version_no,status,master_plan_version_id "
            "FROM sub_plan_versions ORDER BY id"
        ),
        "production_lines": rows(
            "SELECT id,sub_plan_id,status,current_version_id FROM production_lines ORDER BY id"
        ),
        "production_line_versions": rows(
            "SELECT id,production_line_id,sub_plan_version_id,version_no,status "
            "FROM production_line_versions ORDER BY id"
        ),
    }


def _valid_model_result():
    return ({
        "plan": _plan(6),
        "summary": "依据已确认经营诊断生成的母计划草稿。",
        "assumptions": [],
        "risks": [],
        "pending_confirmations": ["请确认首月发布频率"],
        "resource_basis": ["已登记的小红书账号及现有素材"],
    }, "t8-d-deterministic-model")


@pytest.mark.parametrize("existing_effective", [False, True], ids=["first-generation", "existing-effective"])
@pytest.mark.parametrize(
    ("failure_kind", "expected_status"),
    [("provider", 502), ("invalid-schema", 422)],
)
def test_ai_master_plan_api_failure_is_atomic_and_retryable(
    master_plan_client, monkeypatch, existing_effective, failure_kind, expected_status,
):
    from src.core import master_plan_generation

    client, conn, _cfg = master_plan_client
    # Seed all business inputs before confirming the diagnosis: the source
    # fingerprint intentionally invalidates when business context changes.
    previous_master_id = _seed_effective_plan_graph(conn) if existing_effective else None
    _confirm_diagnosis(client, monkeypatch, conn)
    before = _plan_graph_snapshot(conn)

    async def injected_failure(_cfg, _prompt):
        if failure_kind == "provider":
            raise RuntimeError("injected provider timeout")
        return ({
            "plan": {
                "topic_count_per_platform": 0,
                "enabled_platforms": ["xhs"],
            },
            "summary": "invalid plan",
            "assumptions": [],
            "risks": [],
            "pending_confirmations": [],
            "resource_basis": [],
        }, "t8-d-invalid-model")

    monkeypatch.setattr(master_plan_generation, "_call_model", injected_failure)
    failed = client.post("/api/plans/ai-draft")
    assert failed.status_code == expected_status, failed.text
    assert _plan_graph_snapshot(conn) == before

    # A failed attempt must not consume a version number or leave an orphan row.
    assert conn.execute(
        "SELECT COUNT(*) FROM master_plan_versions"
    ).fetchone()[0] == len(before["masters"])
    if previous_master_id is None:
        assert conn.execute(
            "SELECT active_version_id FROM plan_settings WHERE id=1"
        ).fetchone() is None
    else:
        assert conn.execute(
            "SELECT active_version_id FROM plan_settings WHERE id=1"
        ).fetchone()[0] == previous_master_id

    async def injected_success(_cfg, _prompt):
        return _valid_model_result()

    monkeypatch.setattr(master_plan_generation, "_call_model", injected_success)
    retried = client.post("/api/plans/ai-draft")
    assert retried.status_code == 201, retried.text
    result = retried.json()
    assert result["effective"] is False
    assert result["requires_review"] is True
    assert result["draft"]["status"] == "draft"
    assert result["generation"]["diagnosis_report_id"] is not None

    after = _plan_graph_snapshot(conn)
    assert len(after["masters"]) == len(before["masters"]) + 1
    assert after["subplans"] == before["subplans"]
    assert after["production_lines"] == before["production_lines"]
    if previous_master_id is not None:
        effective = conn.execute(
            "SELECT id FROM master_plan_versions WHERE status='effective'"
        ).fetchall()
        assert [int(row["id"]) for row in effective] == [previous_master_id]
        assert after["settings"] == before["settings"]


class _ThreadLocalConnections:
    """Give each concurrent API worker its own SQLite connection to the same temp DB."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.local = threading.local()
        self.connections = []
        self.lock = threading.Lock()

    def _connection(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self.local.conn = conn
            with self.lock:
                self.connections.append(conn)
        return conn

    def __getattr__(self, name):
        return getattr(self._connection(), name)

    def close_all(self):
        with self.lock:
            connections = list(self.connections)
        for conn in connections:
            conn.close()


def test_concurrent_master_plan_review_has_one_winner_and_stable_409(
    master_plan_client, monkeypatch,
):
    from src.core import master_plan_orchestrator
    from src.core.plan_version_store import PlanVersionStore

    client, conn, cfg = master_plan_client
    store = PlanVersionStore(conn)
    effective = store.create_master_draft(_plan(5), author="t8-d-test", source="system")
    store.activate_master_version(int(effective["id"]), reviewer="t8-d-test")
    candidate = store.create_master_draft(
        _plan(7), author="t8-d-test", source="system",
        based_on_version_id=int(effective["id"]),
    )
    before_count = int(conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0])
    before_pointer = int(conn.execute(
        "SELECT active_version_id FROM plan_settings WHERE id=1"
    ).fetchone()[0])

    # Prevent provider calls after approval; this test targets the master-version transition.
    async def no_child_generation(*_args, **_kwargs):
        return {"status": "skipped", "items": []}

    monkeypatch.setattr(
        master_plan_orchestrator, "generate_subplan_drafts", no_child_generation,
    )

    proxy = _ThreadLocalConnections(cfg.paths.db_file)
    api_main._g_ctx["conn"] = proxy
    barrier = threading.Barrier(2)
    original_activate = PlanVersionStore.activate_master_version

    def synchronized_activate(self, version_id, *, reviewer, **kwargs):
        barrier.wait(timeout=10)
        return original_activate(self, version_id, reviewer=reviewer, **kwargs)

    monkeypatch.setattr(PlanVersionStore, "activate_master_version", synchronized_activate)

    app = api_main._build_app()
    clients = [client, TestClient(app, base_url="http://127.0.0.1")]
    # Reuse the fixture's authenticated headers without sharing its DB connection.
    for item in clients:
        item.headers.update({"X-Tenant-Id": "default"})
        login = item.post("/api/auth/login", json={"password": "Test-access9!"})
        assert login.status_code == 200

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(item.post, f"/api/plans/{candidate['id']}/review", json={"action": "approve"})
                for item in clients
            ]
            responses = [future.result(timeout=20) for future in futures]
    finally:
        for item in clients:
            item.close()
        proxy.close_all()
        api_main._g_ctx["conn"] = conn

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409], [(response.status_code, response.text) for response in responses]
    conflict = next(response for response in responses if response.status_code == 409)
    assert "草稿" in conflict.json()["detail"] or "最新" in conflict.json()["detail"]

    assert int(conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0]) == before_count
    effective_rows = conn.execute(
        "SELECT id,status FROM master_plan_versions WHERE status='effective'"
    ).fetchall()
    assert [(int(row["id"]), row["status"]) for row in effective_rows] == [
        (int(candidate["id"]), "effective")
    ]
    assert int(conn.execute(
        "SELECT active_version_id FROM plan_settings WHERE id=1"
    ).fetchone()[0]) == int(candidate["id"])
    assert before_pointer == int(effective["id"])


def test_master_plan_review_rejects_stale_and_cross_tenant_version_ids(
    master_plan_client, monkeypatch,
):
    from src.core import master_plan_orchestrator

    client, conn, _cfg = master_plan_client
    store = PlanVersionStore(conn)
    first = store.create_master_draft(_plan(5), author="t8-d-test", source="system")
    second = store.create_master_draft(
        _plan(6), author="t8-d-test", source="system",
        based_on_version_id=int(first["id"]),
    )
    async def no_child_generation(*_args, **_kwargs):
        return {"status": "skipped", "items": []}
    monkeypatch.setattr(
        master_plan_orchestrator, "generate_subplan_drafts", no_child_generation,
    )

    stale = client.post(f"/api/plans/{first['id']}/review", json={"action": "approve"})
    assert stale.status_code == 409
    assert "最新" in stale.json()["detail"]
    assert conn.execute(
        "SELECT status FROM master_plan_versions WHERE id=?", (first["id"],)
    ).fetchone()["status"] == "draft"

    # A tenant-scoped request carrying another tenant's context is rejected before
    # the target version can be read or mutated.
    before = _plan_graph_snapshot(conn)
    cross_tenant = client.post(
        f"/api/plans/{second['id']}/review",
        json={"action": "approve"},
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert cross_tenant.status_code == 409
    assert _plan_graph_snapshot(conn) == before

    duplicate = client.post(f"/api/plans/{second['id']}/review", json={"action": "approve"})
    assert duplicate.status_code == 200, duplicate.text
    repeated = client.post(f"/api/plans/{second['id']}/review", json={"action": "approve"})
    assert repeated.status_code == 409
    assert int(conn.execute(
        "SELECT COUNT(*) FROM master_plan_versions"
    ).fetchone()[0]) == 2
