"""#110 可靠性改造 × #112 子计划业务能力的联合回归。

覆盖四个整合接缝：
1. 事务边界：子计划版本 + 阶段同事务写入，任一阶段非法必须整体回滚。
2. 租户隔离：缺失/错配 X-Tenant-Id 的写请求在路由前被拒绝，不落任何轮次。
3. 计划版本绑定：轮次固定绑定当次生效的子计划版本/阶段，后续修订草稿不得改写它。
4. 任务执行：autogrow 开轮进入全局任务队列，并由 worker 在任务租户上下文内执行。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.adapters.base import Platform
from src.api import main as api_main
from src.core.autogrow import AutogrowController
from src.core.guard.ultimate_goal_checker import UltimateGoalChecker
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.plan_version_store import PlanVersionError, PlanVersionStore
from src.core.state_machine import DraftStateMachine, RoundStateMachine
from src.core.task_queue import GlobalTaskQueue
from src.core.task_worker import GlobalTaskWorker

PLAN_BASE = {
    "topic_count_per_platform": 5,
    "enabled_platforms": ["xhs"],
    "dimension_allocation": {"AI教育": 5},
    "content_format": "image_text",
}


@pytest.fixture()
def client(cfg, conn, seed, test_auth):
    """注入 tmp 依赖的 TestClient（不跑 lifespan），并建立带租户头的受保护会话。"""
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
        yield test_auth(TestClient(app, base_url="http://127.0.0.1")), conn, seed
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


def _approved_subplan(conn, sub_id: str = "integration-sub"):
    """建一套生效母计划 + 生效子计划 + 可执行阶段，返回（store, master, version, stage）。"""
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        sub_id, master_plan_version_id=int(master["id"]),
        platform="xhs", title="联调子计划", author="test",
    )
    version = store.create_subplan_version(
        sub_id, {"title": "联调子计划", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[{"title": "阶段一", "target_count": 6}],
    )
    store.activate_subplan_version(int(version["id"]), reviewer="test")
    stage = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (int(version["id"]),),
    ).fetchone()
    return store, master, version, stage


# ---------------------------------------------------------------- 1. 事务边界

def test_subplan_version_creation_rolls_back_every_row_on_invalid_stage(cfg, conn):
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        "tx-sub", master_plan_version_id=int(master["id"]),
        platform="xhs", title="事务子计划", author="test",
    )
    before_versions = int(conn.execute(
        "SELECT COUNT(*) FROM sub_plan_versions WHERE sub_plan_id='tx-sub'"
    ).fetchone()[0])

    with pytest.raises(PlanVersionError):
        store.create_subplan_version(
            "tx-sub", {"title": "事务子计划", "platform": "xhs"},
            master_plan_version_id=int(master["id"]),
            stages=[
                {"title": "合法阶段", "target_count": 3},
                {"title": "非法阶段", "target_count": 3, "end_date": "not-a-date"},
            ],
        )

    # 第一个阶段已进入事务，但第二个阶段非法 => 版本和全部阶段都必须回滚。
    assert int(conn.execute(
        "SELECT COUNT(*) FROM sub_plan_versions WHERE sub_plan_id='tx-sub'"
    ).fetchone()[0]) == before_versions
    assert int(conn.execute(
        "SELECT COUNT(*) FROM sub_plan_stages WHERE title IN ('合法阶段','非法阶段')"
    ).fetchone()[0]) == 0
    identity = conn.execute(
        "SELECT current_version_id,status FROM sub_plans WHERE id='tx-sub'"
    ).fetchone()
    assert identity["current_version_id"] is None and identity["status"] == "draft"


# ---------------------------------------------------------------- 2. 租户隔离

def test_tenant_context_is_enforced_before_any_round_is_created(client):
    tc, conn, _ = client
    _approved_subplan(conn)
    before = int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0])
    url = "/api/subplans/integration-sub/rounds"

    # 写请求缺少账号组上下文：路由前拒绝。
    del tc.headers["X-Tenant-Id"]
    missing = tc.post(url, json={})
    assert missing.status_code == 409
    assert missing.json()["code"] == "TENANT_CONTEXT_REQUIRED"

    # 请求携带了与运行时不一致的账号组：同样拒绝，避免串租户写库。
    tc.headers["X-Tenant-Id"] = "default"
    changed = tc.post(url, headers={"X-Tenant-Id": "tenant-b"}, json={})
    assert changed.status_code == 409
    assert changed.json()["code"] == "TENANT_CONTEXT_CHANGED"

    assert int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0]) == before


# ---------------------------------------------------------------- 3. 计划版本绑定

def test_round_is_pinned_to_effective_version_and_survives_new_draft(client):
    tc, conn, _ = client
    store, master, version, stage = _approved_subplan(conn)

    created = tc.post("/api/subplans/integration-sub/rounds", json={"topic_count": 3})
    assert created.status_code == 201
    round_id = int(created.json()["round_id"])

    binding_sql = (
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id,"
        "sub_plan_snapshot,plan_attribution_status FROM spiral_rounds WHERE id=?"
    )
    bound = conn.execute(binding_sql, (round_id,)).fetchone()
    assert int(bound["master_plan_version_id"]) == int(master["id"])
    assert bound["sub_plan_id"] == "integration-sub"
    assert int(bound["sub_plan_version_id"]) == int(version["id"])
    assert int(bound["sub_plan_stage_id"]) == int(stage["id"])
    assert bound["plan_attribution_status"] == "resolved"
    assert json.loads(bound["sub_plan_snapshot"])["title"] == "联调子计划"

    # 修改子计划只产生新的草稿版本，审核生效前不得改写既有轮次。
    revised = tc.patch("/api/subplans/integration-sub", json={
        "based_on_version_id": int(version["id"]),
        "expected_version_no": int(version["version_no"]),
        "snapshot": {"title": "联调子计划（修订）", "platform": "xhs"},
        "stages": [{"title": "阶段二", "target_count": 4}],
        "change_note": "联合回归修订",
    })
    assert revised.status_code == 200
    draft = revised.json()
    assert draft["status"] == "draft"
    assert int(draft["version_no"]) == int(version["version_no"]) + 1

    identity = conn.execute(
        "SELECT current_version_id,status FROM sub_plans WHERE id='integration-sub'"
    ).fetchone()
    assert int(identity["current_version_id"]) == int(version["id"])
    assert identity["status"] == "active"

    after = conn.execute(binding_sql, (round_id,)).fetchone()
    assert tuple(after) == tuple(bound)

    attribution = tc.get(f"/api/rounds/{round_id}/plan-attribution")
    assert attribution.status_code == 200
    body = attribution.json()
    assert int(body["sub_plan_version_id"]) == int(version["id"])
    assert int(body["sub_plan_stage_id"]) == int(stage["id"])
    assert body["plan_attribution_status"] == "resolved"
    assert body["sub_plan_snapshot"]["title"] == "联调子计划"
    assert body["sub_plan_stage_snapshot"]["title"] == "阶段一"




def test_completed_stage_with_later_failed_effect_review_blocks_production(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        "completed-review-sub", master_plan_version_id=int(master["id"]),
        platform="xhs", title="已完成阶段复核", author="test",
    )
    version = store.create_subplan_version(
        "completed-review-sub", {"title": "已完成阶段复核", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[{
            "stage_key": "validate", "title": "效果验证", "target_count": 1,
            "effect_evaluation": "有效反馈率达到目标",
        }],
    )
    store.activate_subplan_version(int(version["id"]), reviewer="test")
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (int(version["id"]),)
    ).fetchone()
    conn.execute(
        "UPDATE sub_plan_stages SET status='completed',effect_status='failed' WHERE id=?",
        (int(stage["id"]),),
    )
    conn.commit()

    from src.core.subplan_production import active_subplan_stage
    active = active_subplan_stage(conn)

    assert active is not None
    assert active["blocker"] == "strategy_condition_requires_adjustment"
    assert int(active["id"]) == int(stage["id"])


def test_invalid_effect_review_does_not_mutate_stage_or_leave_partial_review(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        "effect-review-sub", master_plan_version_id=int(master["id"]),
        platform="xhs", title="效果审核子计划", author="test",
    )
    version = store.create_subplan_version(
        "effect-review-sub", {"title": "效果审核子计划", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[{
            "stage_key": "validate", "title": "效果验证", "target_count": 1,
            "effect_evaluation": "有效反馈率达到目标",
        }],
    )
    store.activate_subplan_version(int(version["id"]), reviewer="test")
    stage = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=?", (int(version["id"]),)
    ).fetchone()

    response = tc.post(
        f"/api/subplans/effect-review-sub/stages/{int(stage['id'])}/condition-review",
        json={
            "condition_type": "effect", "decision": "failed",
            "reason": "效果未达标", "confidence": "invalid",
        },
    )

    assert response.status_code == 422
    current = conn.execute(
        "SELECT effect_status FROM sub_plan_stages WHERE id=?", (int(stage["id"]),)
    ).fetchone()
    assert current["effect_status"] == "pending"
    assert int(conn.execute(
        "SELECT COUNT(*) FROM strategy_step_condition_reviews WHERE stage_id=?",
        (int(stage["id"]),),
    ).fetchone()[0]) == 0
    assert int(conn.execute(
        "SELECT COUNT(*) FROM strategy_effect_reviews WHERE stage_id=?",
        (int(stage["id"]),),
    ).fetchone()[0]) == 0


def test_manual_round_open_blocks_missing_predecessor_outcome_with_reason(client):
    tc, conn, seed = client
    round_id = seed.round()
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        "dependency-sub", master_plan_version_id=int(master["id"]),
        platform="xhs", title="依赖门禁子计划", author="test",
    )
    version = store.create_subplan_version(
        "dependency-sub", {"title": "依赖门禁子计划", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[
            {"stage_key": "research", "title": "调研", "target_count": 1},
            {"stage_key": "production", "title": "生产", "target_count": 2,
             "dependencies": ["research"], "entry_condition_met": True},
        ],
    )
    store.activate_subplan_version(int(version["id"]), reviewer="test")
    stages = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order",
        (int(version["id"]),),
    ).fetchall()
    store.update_stage_status(int(stages[0]["id"]), "in_progress")
    conn.execute(
        "INSERT INTO contents(id,round_id,platform,title,body) "
        "VALUES('dependency-research-content',?,'xhs','调研内容','已发布的调研内容')",
        (round_id,),
    )
    conn.execute(
        "INSERT INTO drafts(id,content_id,platform,user_confirmed_at) "
        "VALUES('dependency-research-published','dependency-research-content','xhs','2026-11-03')"
    )
    conn.execute(
        "UPDATE spiral_rounds SET sub_plan_id='dependency-sub',sub_plan_version_id=?,sub_plan_stage_id=? WHERE id=?",
        (int(version["id"]), int(stages[0]["id"]), round_id),
    )
    conn.commit()
    store.update_stage_status(int(stages[0]["id"]), "completed")

    before = int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0])
    response = tc.post("/api/subplans/dependency-sub/rounds", json={"topic_count": 2})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_step_blocked"
    assert detail["stage_key"] == "production"
    assert detail["dependencies"] == ["research"]
    assert "实际效果记录" in detail["message"]
    assert int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0]) == before


def test_manual_round_open_blocks_unmet_entry_condition(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        "entry-sub", master_plan_version_id=int(master["id"]),
        platform="xhs", title="进入条件子计划", author="test",
    )
    version = store.create_subplan_version(
        "entry-sub", {"title": "进入条件子计划", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[{"stage_key": "prepare", "title": "准备", "target_count": 2,
                 "entry_condition_met": False}],
    )
    store.activate_subplan_version(int(version["id"]), reviewer="test")

    before = int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0])
    response = tc.post("/api/subplans/entry-sub/rounds", json={"topic_count": 2})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "strategy_step_blocked"
    assert detail["stage_key"] == "prepare"
    assert "进入条件" in detail["message"]
    assert int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0]) == before


# ---------------------------------------------------------------- 4. 任务执行

class _FakeExecutor:
    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    def _resolve_platforms(self, _):
        return [Platform.XHS]

    def _platform_broken(self, _):
        return False

    async def run(self, round_id, topic_count, platform_values, save_drafts, feedback):
        self.calls.append((round_id, topic_count))
        self.conn.execute(
            "UPDATE spiral_rounds SET status='awaiting_publish' WHERE id=?", (round_id,),
        )
        self.conn.commit()
        return {"drafts_saved": 2, "final_status": "awaiting_publish"}


class _FakePvm:
    def load_plan(self):
        return {}, None, None, "default"

    def build_plan(self, **kw):
        return kw

    def record(self, *a, **kw):
        return "sig"


def test_autogrow_round_is_queued_then_executed_by_worker(cfg, conn, seed, tmp_path):
    store, master, version, stage = _approved_subplan(conn, "exec-sub")

    # 自动闭环只接续已经收口过的一轮。
    seeded = seed.round(status="completed")
    ended = (datetime.now() - timedelta(hours=48)).isoformat()
    conn.execute(
        "UPDATE spiral_rounds SET transition_log=?, created_at=? WHERE id=?",
        (json.dumps([{"to": "completed", "at": ended}]), ended.replace("T", " ")[:19], seeded),
    )
    conn.commit()

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()

    def enqueue_task(*, round_id, topic_count, platforms, save_drafts, feedback):
        return queue.enqueue(
            task_type="round_run", tenant_id="tenant-a", aggregate_id=str(round_id),
            payload={
                "round_id": round_id, "operation": "run",
                "topic_count": topic_count, "platforms": platforms,
            },
        )

    controller = AutogrowController(
        cfg, conn, _FakeExecutor(conn), pvm=_FakePvm(),
        guard=UltimateGoalChecker(cfg), enqueue_task=enqueue_task,
    )
    result = asyncio.run(controller.tick(force=True))
    assert result["action"] == "queued"
    round_id = int(result["round_id"])
    assert queue.get(result["task_id"]).status == "queued"

    bound = conn.execute(
        "SELECT trigger_source,master_plan_version_id,sub_plan_id,sub_plan_version_id,"
        "sub_plan_stage_id,plan_attribution_status FROM spiral_rounds WHERE id=?",
        (round_id,),
    ).fetchone()
    assert bound["trigger_source"] == "auto"
    assert int(bound["master_plan_version_id"]) == int(master["id"])
    assert bound["sub_plan_id"] == "exec-sub"
    assert int(bound["sub_plan_version_id"]) == int(version["id"])
    assert int(bound["sub_plan_stage_id"]) == int(stage["id"])
    assert bound["plan_attribution_status"] == "resolved"

    stop = asyncio.Event()
    observed = {}

    class _WorkerExecutor:
        async def run(self, round_id, **kwargs):
            observed["round_id"] = round_id
            observed["kwargs"] = kwargs
            stop.set()
            return {"round_id": round_id, "final_status": "completed"}

    tenant_cfg = SimpleNamespace(
        paths=SimpleNamespace(data_dir=str(tmp_path / "tenant-a" / "data")),
        resolve_path=lambda value: value,
    )
    context = {
        "cfg": tenant_cfg,
        "executor": _WorkerExecutor(),
        "scheduler": SimpleNamespace(shutdown=lambda: None),
        "conn": SimpleNamespace(close=lambda: None),
    }
    worker = GlobalTaskWorker(
        queue, lambda tenant_id: context,
        poll_interval_seconds=0.01, lease_seconds=30, heartbeat_seconds=1,
    )
    asyncio.run(worker.run(stop))

    saved = queue.get(result["task_id"])
    assert saved is not None and saved.status == "succeeded"
    assert saved.result == {"round_id": round_id, "final_status": "completed"}
    assert observed["round_id"] == round_id
    assert observed["kwargs"]["platform_values"] == ["xhs"]
    assert observed["kwargs"]["feedback"] is None
