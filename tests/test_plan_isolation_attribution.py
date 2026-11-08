"""P0/P1 验收：母计划隔离、轮次计划绑定、内容归属、孤儿内容与阶段指标。"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.plan_version_store import PlanVersionStore
from src.core.production_line_store import ProductionLineStore
from src.core.state_machine import DraftStateMachine, RoundStateMachine


@pytest.fixture()
def api_client(cfg, conn, test_auth):
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


def _strategic_plan() -> dict:
    return {
        "topic_count_per_platform": 2,
        "dimension_allocation": {"AI教育": 2},
        "enabled_platforms": ["douyin", "xhs"],
        "content_format": "image_text",
    }


def _effective_master(store: PlanVersionStore, *, per_platform: dict | None = None) -> int:
    plan = _strategic_plan()
    if per_platform is not None:
        plan["per_platform"] = per_platform
        plan["cadence"] = {"round_interval_days": 3}
    draft = store.create_master_draft(plan)
    return int(store.activate_master_version(draft["id"], reviewer="test")["id"])


def _activate_production_line(conn, sub_plan_id: str, version_id: int) -> int:
    rows = conn.execute(
        "SELECT stage_key,target_count FROM sub_plan_stages "
        "WHERE sub_plan_version_id=? ORDER BY stage_order,id",
        (version_id,),
    ).fetchall()
    line_store = ProductionLineStore(conn)
    draft = line_store.create_draft(sub_plan_id, version_id, {
        "title": "测试生产线",
        "stages": [
            {"stage_key": row["stage_key"], "batch_size": 1, "production_goal": "测试"}
            for row in rows
        ],
    })
    return int(line_store.activate(draft["id"])["id"])


def test_signature_ignores_platform_execution_config():
    manager = PlanVersionManager(None, None)
    base = _strategic_plan()
    changed_platform = dict(base, per_platform={"xhs": {"topic_count": 3}})
    assert manager.compute_signature(base) == manager.compute_signature(changed_platform)

    changed_strategy = dict(base, topic_count_per_platform=9)
    assert manager.compute_signature(base) != manager.compute_signature(changed_strategy)


def test_master_snapshot_excludes_runtime_and_projection_keeps_it(cfg, conn):
    store = PlanVersionStore(conn)
    version_id = _effective_master(
        store, per_platform={"xhs": {"topic_count": 3, "publish_slots": ["20:00"]}},
    )
    snapshot = json.loads(conn.execute(
        "SELECT plan_snapshot FROM master_plan_versions WHERE id=?", (version_id,)
    ).fetchone()["plan_snapshot"])
    assert "per_platform" not in snapshot and "cadence" not in snapshot

    runtime = store.load_runtime_config(version_id)
    assert runtime["per_platform"]["xhs"]["topic_count"] == 3
    assert runtime["cadence"]["round_interval_days"] == 3

    projection = json.loads(conn.execute(
        "SELECT plan_snapshot FROM plan_settings WHERE id=1"
    ).fetchone()["plan_snapshot"])
    assert projection["per_platform"]["xhs"]["publish_slots"] == ["20:00"]
    assert projection["topic_count_per_platform"] == 2


def test_platform_only_change_pauses_only_that_platform(cfg, conn):
    store = PlanVersionStore(conn)
    version_id = _effective_master(store, per_platform={
        "xhs": {"topic_count": 2}, "douyin": {"topic_count": 2},
    })
    for platform in ("xhs", "douyin"):
        sub_id = f"platform-only-{platform}"
        store.create_subplan(sub_id, master_plan_version_id=version_id,
                             platform=platform, title=platform)
        version = store.create_subplan_version(
            sub_id, {"title": platform, "platform": platform},
            master_plan_version_id=version_id,
            stages=[{"title": "阶段一", "target_count": 1}],
        )
        store.activate_subplan_version(version["id"], reviewer="test")
        conn.execute("UPDATE sub_plans SET status='active' WHERE id=?", (sub_id,))
    conn.commit()

    new_plan = _strategic_plan()
    new_plan["per_platform"] = {"xhs": {"topic_count": 3}, "douyin": {"topic_count": 2}}
    draft = store.create_master_draft(new_plan, based_on_version_id=version_id)
    store.activate_master_version(draft["id"], reviewer="test")

    statuses = dict(conn.execute(
        "SELECT id, status FROM sub_plans WHERE id IN ('platform-only-xhs','platform-only-douyin')"
    ).fetchall())
    assert statuses["platform-only-xhs"] == "paused"
    assert statuses["platform-only-douyin"] == "active"


def test_round_requires_binding_when_effective_master_exists(api_client):
    client, conn, _cfg = api_client
    _effective_master(PlanVersionStore(conn))
    response = client.post("/api/rounds", json={"topic_count": 2})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLAN_BINDING_REQUIRED"


def test_bound_round_writes_attribution_and_orphans_on_failure(cfg, conn):
    store = PlanVersionStore(conn)
    master_id = _effective_master(store)
    sub_id = "attribution-subplan"
    store.create_subplan(sub_id, master_plan_version_id=master_id,
                         platform="xhs", title="归属测试")
    version = store.create_subplan_version(
        sub_id, {"title": "归属测试", "platform": "xhs"},
        master_plan_version_id=master_id,
        stages=[{"title": "阶段一", "target_count": 1}],
    )
    store.activate_subplan_version(version["id"], reviewer="test")
    conn.execute("UPDATE sub_plans SET status='active' WHERE id=?", (sub_id,))
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()
    line_version_id = _activate_production_line(conn, sub_id, int(version["id"]))
    conn.commit()

    key = "attribution-round-open"
    store.reserve_round_open(
        key, "attribution-hash", master_plan_version_id=master_id,
        sub_plan_id=sub_id, sub_plan_version_id=int(version["id"]),
        sub_plan_stage_id=int(stage["id"]),
    )
    created = store.create_round_once(
        key, content_variants=[{"platform": "xhs"}], trigger_source="manual",
        production_line_version_id=line_version_id,
    )
    round_id = int(created["round_id"])
    executor = api_main.RoundExecutor  # noqa: F841  (保持模块可导入的契约)

    # 轮次已绑定五个计划字段
    round_row = conn.execute(
        "SELECT master_plan_version_id, sub_plan_id, sub_plan_version_id, "
        "sub_plan_stage_id, production_line_version_id FROM spiral_rounds WHERE id=?",
        (round_id,),
    ).fetchone()
    assert tuple(round_row) == (master_id, sub_id, int(version["id"]),
                                int(stage["id"]), line_version_id)

    # 模拟按轮次归属写入内容与草稿
    conn.execute(
        "INSERT INTO contents(id,round_id,platform,title,body,master_plan_version_id,"
        "sub_plan_id,sub_plan_version_id,sub_plan_stage_id,production_line_version_id) "
        "VALUES('orphan-content',?,'xhs','t','b',?,?,?,?,?)",
        (round_id, master_id, sub_id, int(version["id"]), int(stage["id"]), line_version_id),
    )
    conn.execute(
        "INSERT INTO drafts(id,content_id,platform,status) VALUES('orphan-draft','orphan-content','xhs','draft_pending')"
    )
    conn.commit()

    sm = RoundStateMachine(conn)
    assert sm.transition(round_id, "producing", "test") is True
    assert sm.transition(round_id, "failed", "test") is True
    lifecycle = conn.execute(
        "SELECT lifecycle_status FROM contents WHERE id='orphan-content'"
    ).fetchone()["lifecycle_status"]
    assert lifecycle == "orphaned"


def test_stage_progress_uses_metric_and_keeps_nulls(cfg, conn):
    store = PlanVersionStore(conn)
    master_id = _effective_master(store)
    sub_id = "stage-metric-subplan"
    store.create_subplan(sub_id, master_plan_version_id=master_id,
                         platform="xhs", title="指标测试")
    version = store.create_subplan_version(
        sub_id, {"title": "指标测试", "platform": "xhs"},
        master_plan_version_id=master_id,
        stages=[{"title": "阶段一", "target_count": 1}, {"title": "阶段二", "target_count": 1}],
    )
    stages = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order,id",
        (version["id"],),
    ).fetchall()

    # 未配置指标 → 仅内容参考
    reference = store.stage_progress(int(stages[0]["id"]))
    assert reference["basis"] == "content_reference"
    assert reference["achievement_rate"] is None

    # 配置指标但无数据 → NULL，不是 0
    conn.execute(
        "UPDATE sub_plan_stages SET target_metric='followers_gained', target_value=100, "
        "metric_scope='delta', baseline_value=10, data_source='account_stats' WHERE id=?",
        (int(stages[1]["id"]),),
    )
    conn.commit()
    progress = store.stage_progress(int(stages[1]["id"]))
    assert progress["basis"] == "metric"
    assert progress["current_value"] is None
    assert progress["achievement_rate"] is None
    assert progress["data_ready"] is False


def test_tenant_create_initializes_database_schema(cfg, tmp_path):
    from src.core.tenant_registry import TenantRegistry

    registry = TenantRegistry(tmp_path, cfg=cfg)
    registry.initialize()
    paths = registry.create("schema-check", "Schema Check")
    conn = sqlite3.connect(paths.db_file)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(contents)").fetchall()}
    finally:
        conn.close()
    assert "contents" in tables and "master_plan_versions" in tables
    assert "sub_plan_stage_id" in columns and "lifecycle_status" in columns


# ---------------------------------------------------------------------------
# 第二轮审查补修（spec §7）
# ---------------------------------------------------------------------------

def _seed_subplan(store: PlanVersionStore, conn, sub_id: str, stages: list[dict]):
    master_id = _effective_master(store)
    store.create_subplan(sub_id, master_plan_version_id=master_id,
                         platform="xhs", title=sub_id)
    version = store.create_subplan_version(
        sub_id, {"title": sub_id, "platform": "xhs"},
        master_plan_version_id=master_id, stages=stages,
    )
    store.activate_subplan_version(version["id"], reviewer="test")
    conn.execute("UPDATE sub_plans SET status='active' WHERE id=?", (sub_id,))
    conn.commit()
    return master_id, version


def _create_bound_round(client, conn, sub_id: str = "api-round-sub"):
    store = PlanVersionStore(conn)
    master_id, version = _seed_subplan(store, conn, sub_id, [{"title": "阶段一", "target_count": 1}])
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()
    line_version_id = _activate_production_line(conn, sub_id, int(version["id"]))
    conn.commit()
    response = client.post("/api/rounds", json={
        "topic_count": 1,
        "sub_plan_id": sub_id,
        "sub_plan_version_id": int(version["id"]),
        "sub_plan_stage_id": int(stage["id"]),
        "production_line_version_id": line_version_id,
    })
    assert response.status_code == 200, response.text
    return store, master_id, sub_id, version, stage, line_version_id, int(response.json()["id"])


def test_api_bound_round_persists_plan_snapshots(api_client):
    client, conn, _cfg = api_client
    store, master_id, sub_id, version, stage, line_version_id, round_id = _create_bound_round(client, conn)

    row = conn.execute(
        "SELECT sub_plan_snapshot, sub_plan_stage_snapshot FROM spiral_rounds WHERE id=?",
        (round_id,),
    ).fetchone()
    assert row["sub_plan_snapshot"]
    assert json.loads(row["sub_plan_stage_snapshot"])["id"] == int(stage["id"])

    integrity = store.validate_integrity()
    assert all(round_id not in issue.get("round_ids", []) for issue in integrity["issues"])


class _PassGuard:
    def check_content(self, *args, **kwargs):
        return type("Outcome", (), {"passes": True, "reason": "ok"})()


def test_promoted_sample_inherits_round_plan_attribution(api_client, tmp_path, monkeypatch):
    from src.core.task_queue import GlobalTaskQueue

    client, conn, _cfg = api_client
    _store, master_id, sub_id, version, stage, line_version_id, round_id = _create_bound_round(
        client, conn, "promote-sub",
    )
    api_main._g_ctx["guard"] = _PassGuard()
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    now = api_main.storage_now().isoformat(sep=" ", timespec="seconds")
    conn.execute(
        "INSERT INTO sample_runs(id,tenant_id,platform,account_description,status,"
        "idempotency_key,created_at) VALUES('run-promote','default','xhs',"
        "'这是一个足够长的账号定位描述','ready','k-promote',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO sample_contents(id,sample_run_id,tenant_id,platform,angle,title,body,"
        "hashtags_json,quality_status,adopted_at,created_at,updated_at) "
        "VALUES('sample-promote','run-promote','default','xhs','角度','标题','正文','[]',"
        "'pass',?,?,?)",
        (now, now, now),
    )
    conn.commit()

    response = client.post("/api/activation/saved/sample-promote/promote", json={"round_id": round_id})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sample_content_id"] == "sample-promote"
    assert body["draft_status"] == "draft_pending_after_success"
    assert body["platform_draft_saved"] is False
    assert body["publication_started"] is False

    task = queue.get(body["task_id"])
    assert task is not None and task.status == "queued"
    assert task.payload["operation"] == "activation_promote"
    assert task.payload["round_id"] == round_id
    assert task.payload["save_drafts"] is False
    binding = task.payload["execution_snapshot"]["round_binding"]
    assert binding == {
        "master_plan_version_id": master_id,
        "sub_plan_id": sub_id,
        "sub_plan_version_id": int(version["id"]),
        "sub_plan_stage_id": int(stage["id"]),
        "sub_plan_snapshot_sha256": binding["sub_plan_snapshot_sha256"],
        "sub_plan_stage_snapshot_sha256": binding["sub_plan_stage_snapshot_sha256"],
    }
    sample = conn.execute(
        "SELECT promoted_task_id,promoted_round_id FROM sample_contents WHERE id='sample-promote'"
    ).fetchone()
    assert tuple(sample) == (body["task_id"], round_id)
    # 入队阶段不得提前创建正式内容或平台草稿；Worker 成功后才会持久化 draft_pending。
    assert conn.execute("SELECT COUNT(*) FROM contents WHERE round_id=?", (round_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM drafts WHERE round_id=?", (round_id,)).fetchone()[0] == 0


def test_adopted_content_draft_keeps_plan_attribution(cfg, conn):
    from src.core.round_executor import RoundExecutor

    now = "2026-11-07 10:00:00"
    conn.execute(
        "INSERT INTO spiral_rounds(round_number,status,topic_ids,content_variants,created_at) "
        "VALUES(9,'created','[]','[]',?)", (now,)
    )
    round_id = int(conn.execute("SELECT MAX(id) FROM spiral_rounds").fetchone()[0])
    conn.execute(
        "INSERT INTO contents(id,round_id,platform,title,body) VALUES('adopt-content',?,'xhs','t','b')",
        (round_id,),
    )
    conn.commit()
    executor = RoundExecutor(cfg, conn, None, None, {}, None, None)
    executor._round_plan_meta = {
        "master_plan_version_id": 11, "sub_plan_id": "adopt-sub",
        "sub_plan_version_id": 22, "sub_plan_stage_id": 33,
        "production_line_version_id": 44,
    }
    draft_id = executor._ensure_draft_row("adopt-content", "xhs")
    row = conn.execute(
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id "
        "FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()
    assert tuple(row) == (11, "adopt-sub", 22, 33)


def test_legacy_attribution_cascades_to_contents_and_drafts(cfg, conn):
    store = PlanVersionStore(conn)
    master_id = _effective_master(store)
    now = "2026-11-07 10:00:00"
    conn.execute(
        "INSERT INTO spiral_rounds(round_number,status,topic_ids,content_variants,created_at,"
        "plan_attribution_status) VALUES(10,'created','[]','[]',?,'pending')", (now,),
    )
    round_id = int(conn.execute("SELECT MAX(id) FROM spiral_rounds").fetchone()[0])
    conn.execute(
        "INSERT INTO contents(id,round_id,platform,title,body) VALUES('legacy-content',?,'xhs','t','b')",
        (round_id,),
    )
    conn.execute(
        "INSERT INTO drafts(id,content_id,platform,status) "
        "VALUES('legacy-draft','legacy-content','xhs','draft_pending')"
    )
    conn.commit()

    store.attribute_legacy_round(round_id, master_id, reviewer="auditor", reason="历史归属补录")

    content_row = conn.execute(
        "SELECT master_plan_version_id FROM contents WHERE id='legacy-content'"
    ).fetchone()
    draft_row = conn.execute(
        "SELECT master_plan_version_id FROM drafts WHERE id='legacy-draft'"
    ).fetchone()
    assert content_row["master_plan_version_id"] == master_id
    assert draft_row["master_plan_version_id"] == master_id


def test_stage_metric_config_roundtrip_and_goal_progress(api_client):
    client, conn, _cfg = api_client
    store = PlanVersionStore(conn)
    _master_id, version = _seed_subplan(
        store, conn, "metric-api-sub", [{"title": "阶段一", "target_count": 1}],
    )
    now = "2026-11-07 10:00:00"
    conn.execute(
        "INSERT INTO goals(id,goal_type,goal_text,target_metric,target_value,metric_scope,"
        "baseline_value,baseline_at,data_source,current_value,status,created_at) "
        "VALUES('goal-stage-metric','stage','目标','followers_gained',100,'delta',20,"
        "'2026-01-01','account_stats',70,'active',?)",
        (now,),
    )
    conn.commit()

    response = client.post("/api/subplans/metric-api-sub/stages", json={
        "based_on_version_id": int(version["id"]),
        "expected_version_no": int(version["version_no"]),
        "stage": {
            "title": "指标阶段", "target_count": 2,
            "target_metric": "followers_gained", "target_value": 100,
            "metric_scope": "delta", "baseline_value": 20,
            "baseline_at": "2026-01-01", "data_source": "account_stats",
            "goal_id": "goal-stage-metric",
        },
    })
    assert response.status_code == 201, response.text
    stage_id = int(response.json()["stages"][-1]["id"])
    row = conn.execute(
        "SELECT target_metric,target_value,metric_scope,baseline_value,data_source,goal_id "
        "FROM sub_plan_stages WHERE id=?", (stage_id,),
    ).fetchone()
    assert tuple(row) == ("followers_gained", 100.0, "delta", 20.0, "account_stats",
                          "goal-stage-metric")

    progress = store.stage_progress(stage_id)
    assert progress["basis"] == "metric"
    assert progress["current_value"] == 70.0
    assert progress["achievement_rate"] == pytest.approx(0.5)


def test_stage_metric_partial_config_rejected(api_client):
    client, conn, _cfg = api_client
    store = PlanVersionStore(conn)
    _master_id, version = _seed_subplan(
        store, conn, "metric-bad-sub", [{"title": "阶段一", "target_count": 1}],
    )
    response = client.post("/api/subplans/metric-bad-sub/stages", json={
        "based_on_version_id": int(version["id"]),
        "expected_version_no": int(version["version_no"]),
        "stage": {"title": "残缺指标", "target_count": 1, "target_metric": "followers_gained"},
    })
    assert response.status_code == 422
