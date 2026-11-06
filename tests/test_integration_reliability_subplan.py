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
from src.core.production_line_store import ProductionLineStore
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


def _approved_subplan(conn, sub_id: str = "integration-sub", master_version_id: int | None = None):
    """建一套生效母计划 + 生效子计划 + 可执行阶段，返回（store, master, version, stage）。"""
    store = PlanVersionStore(conn)
    # The activation trigger freezes the current business profile/catalog. Seed
    # them before creating the effective version; later edits cannot rewrite it.
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO business_profiles(
            id,business_name,introduction,industry,business_model,business_stage,
            operating_goal,field_evidence_json,version_no,updated_at
        ) VALUES(1,'集成测试企业','提供专业内容服务','教育服务','专业服务','成长阶段',
                 '获取目标客户','{}',1,?)
        ON CONFLICT(id) DO UPDATE SET business_name=excluded.business_name,
            introduction=excluded.introduction,industry=excluded.industry,
            business_model=excluded.business_model,business_stage=excluded.business_stage,
            operating_goal=excluded.operating_goal,field_evidence_json=excluded.field_evidence_json,
            version_no=excluded.version_no,updated_at=excluded.updated_at""",
        (now,),
    )
    conn.execute(
        """INSERT INTO business_catalog_items(
            id,entity_type,name,details_json,field_evidence_json,is_active,created_at,updated_at
        ) VALUES('integration-test-offer','service','专业内容咨询','{}','{}',1,?,?)
        ON CONFLICT(id) DO UPDATE SET is_active=1,updated_at=excluded.updated_at""",
        (now, now),
    )
    if master_version_id is None:
        master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
        store.activate_master_version(int(master["id"]), reviewer="test")
    else:
        master = dict(conn.execute(
            "SELECT * FROM master_plan_versions WHERE id=?", (master_version_id,)
        ).fetchone())
    # Production context gate (merged from main T9) requires each effective mother plan
    # to carry an attributed north star version and a confirmed business diagnosis.
    # Seed them idempotently here so every subplan integration test gets a complete
    # production context rather than failing mid-execution with ProductionContextError.
    import json as _json
    master_id = int(master["id"])
    existing_binding = conn.execute(
        "SELECT north_star_version_id,north_star_attribution_status FROM master_plan_versions WHERE id=?",
        (master_id,),
    ).fetchone()
    if (
        existing_binding is None
        or existing_binding["north_star_version_id"] is None
        or existing_binding["north_star_attribution_status"] != "attributed"
    ):
        ns_snapshot = {
            "positioning": "集成测试业务定位",
            "business_direction": "专业内容服务运营",
            "target_users": "教育行业目标客户",
            "core_value": "稳定生产高质量图文内容",
            "long_term_content_direction": "围绕核心业务持续输出教育类内容",
            "business_model": "专业服务",
            "long_term_goals": "建立可持续内容生产体系",
        }
        conn.execute(
            "UPDATE account_group_north_star_versions SET status='superseded',superseded_at=? WHERE status='effective'",
            (now,),
        )
        ns_cur = conn.execute(
            """INSERT INTO account_group_north_star_versions(
                version_no,snapshot_json,status,change_reason,created_by,created_at,effective_at
            ) VALUES(1,?,'effective','集成测试基线','test',?,?)""",
            (_json.dumps(ns_snapshot, ensure_ascii=False), now, now),
        )
        conn.execute(
            """UPDATE master_plan_versions
               SET north_star_version_id=?,north_star_snapshot_json=?,
                   north_star_attribution_status='attributed'
               WHERE id=?""",
            (ns_cur.lastrowid, _json.dumps(ns_snapshot, ensure_ascii=False), master_id),
        )
    has_confirmed_diagnosis = conn.execute(
        "SELECT 1 FROM business_diagnosis_reports WHERE status='confirmed' LIMIT 1"
    ).fetchone()
    if has_confirmed_diagnosis is None:
        conn.execute(
            """INSERT INTO business_diagnosis_reports(
                version_no,status,report_json,source_snapshot_json,source_fingerprint,
                model_name,created_at,created_by,updated_at,confirmed_at,confirmed_by
            ) VALUES(1,'confirmed','{}','{}','integration-test-fp',
                     'test-model',?,'test',?,?,?)""",
            (now, now, now, "test"),
        )
    conn.commit()
    store.create_subplan(
        sub_id, master_plan_version_id=int(master["id"]),
        platform="xhs", title="联调子计划", author="test",
    )
    version = store.create_subplan_version(
        sub_id, {"title": "联调子计划", "platform": "xhs"},
        master_plan_version_id=int(master["id"]),
        stages=[{"title": "阶段一", "target_count": 6}],
    )
    stage = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (int(version["id"]),),
    ).fetchone()
    # Production context now requires a version-bound formal subgoal and enabled
    # production plan. Build those prerequisites in the draft before activation;
    # do not weaken the production gate to accommodate this integration fixture.
    from src.core.subgoal_store import SubGoalStore
    goals = SubGoalStore(conn)
    goal = goals.create_goal(sub_id, int(version["id"]), {
        "goal_type": "quantity",
        "goal_order": 1,
        "priority": 3,
        "title": "完成阶段一图文生产",
        "description": "联合回归正式生产目标",
        "target_count": int(stage["target_count"]),
        "acceptance_criteria": "达到阶段目标数量并完成业务验收",
        "requires_production": True,
        "stage_id": int(stage["id"]),
    })
    goals.save_production_plan(sub_id, str(goal["id"]), {
        "content_direction": "围绕已确认业务定位生产图文内容",
        "suggested_count": int(stage["target_count"]),
        "resource_requirements": {},
        "priority": 3,
        "execution_conditions": "母计划、子计划和生产线均已审核生效",
        "acceptance_relation": "以阶段目标数量和验收证据为准",
    })
    store.activate_subplan_version(int(version["id"]), reviewer="test")
    line_store = ProductionLineStore(conn)
    line_draft = line_store.create_draft(
        sub_id, int(version["id"]),
        {
            "title": "联调生产线",
            "stages": [{
                "stage_key": str(stage["stage_key"]),
                "batch_size": int(stage["target_count"]),
                "production_goal": str(stage["title"]),
                "instructions": "遵循已审核策略步骤执行",
            }],
        },
        generation_source="manual", author="test",
    )
    line_store.activate(int(line_draft["id"]), reviewer="test")
    # API middleware rolls back any transaction left open at request teardown.
    # This fixture seeds state directly, so commit it before the first TestClient request.
    conn.commit()
    return store, master, version, stage


def _activate_test_production_line(conn, sub_plan_id: str, sub_plan_version_id: int):
    stages = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order",
        (sub_plan_version_id,),
    ).fetchall()
    draft = ProductionLineStore(conn).create_draft(
        sub_plan_id, sub_plan_version_id,
        {"title": "依赖门禁测试生产线", "stages": [{
            "stage_key": str(stage["stage_key"]),
            "batch_size": int(stage["target_count"]),
            "production_goal": str(stage["title"]),
        } for stage in stages]},
        generation_source="manual", author="test",
    )
    ProductionLineStore(conn).activate(int(draft["id"]), reviewer="test")


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

@pytest.mark.asyncio
async def test_round_is_pinned_to_effective_version_and_survives_new_draft(client, monkeypatch):
    tc, conn, _ = client
    # Production-task creation is intentionally fail-closed unless the business
    # context is complete. Seed the same profile/North-Star/confirmed-diagnosis
    # prerequisites as the full T8 API happy path rather than bypassing the gate.
    from src.api import business_diagnosis
    from src.core.business_diagnosis import collect_business_context

    async def fake_diagnosis(conn, cfg):
        context = collect_business_context(conn, cfg)
        return (
            {"business_summary": {"claim": "版本绑定测试业务", "classification": "fact"},
             "assumptions": [], "source_references": []},
            context["context"], "subplan-version-test-model",
        )

    monkeypatch.setattr(business_diagnosis, "generate_diagnosis", fake_diagnosis)
    profile = tc.put("/api/business-profile", json={
        "business_name": "子计划版本绑定验收业务", "industry": "测试行业",
        "business_model": "图文内容运营", "business_stage": "测试阶段",
        "operating_goal": "完成图文内容生产",
    })
    assert profile.status_code == 200, profile.text
    # business_profile_snapshot 触发器在母计划批准瞬间冻结 catalog 状态；必须在批准前 seed
    offering = tc.post("/api/business-profile/items/service", json={
        "name": "联调业务内容服务",
        "details": {"scope": "专业内容生产", "delivery": "四平台图文交付"},
    })
    assert offering.status_code == 200, offering.text
    north_star = tc.put("/api/north-star", json={
        "change_reason": "建立版本绑定验收账号组北极星",
        "snapshot": {
            "positioning": "测试业务定位", "business_direction": "图文内容运营",
            "target_users": "目标测试用户", "core_value": "稳定生产高质量内容",
            "long_term_content_direction": "围绕核心业务持续输出",
            "business_model": "内容营销", "long_term_goals": "建立可持续内容生产体系",
        },
    })
    assert north_star.status_code == 200, north_star.text
    analyzed = tc.post("/api/business-diagnosis/analyze")
    assert analyzed.status_code == 201, analyzed.text
    diagnosis_id = analyzed.json()["report"]["id"]
    confirmed = tc.post(f"/api/business-diagnosis/{diagnosis_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    master_draft = tc.post("/api/plans", json={
        "topic_count_per_platform": 1, "dimension_allocation": {},
        "enabled_platforms": ["xhs"], "content_format": "image_text",
        "per_platform": {"xhs": {"topic_count": 1, "publish_slots": ["20:00"]}},
        "cadence": {"round_interval_days": 1, "rounds_planned": 1},
        "change_note": "版本绑定测试母计划",
    })
    assert master_draft.status_code == 200, master_draft.text
    master_id = int(master_draft.json()["draft"]["id"])
    master_review = tc.post(f"/api/plans/{master_id}/review", json={"action": "approve"})
    assert master_review.status_code == 200, master_review.text
    store, master, version, stage = _approved_subplan(conn, master_version_id=master_id)

    # Multi-item batches conflict with the tenant's single-active-line invariant.
    # Reject before opening a round, rather than failing halfway through task inserts.
    rounds_before = int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0])
    batch = tc.post("/api/subplans/integration-sub/rounds", json={"topic_count": 3})
    assert batch.status_code == 409
    assert "topic_count 必须为 1" in batch.json()["detail"]
    assert int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0]) == rounds_before
    # A rejected multi-item request must not revoke the approved authorization
    # or mutate the active production-line binding before the next valid request.
    state_after_rejection = conn.execute(
        """SELECT p.status AS subplan_status,v.status AS version_status,
                  m.status AS master_status,pl.status AS line_status,
                  plv.status AS line_version_status,
                  plv.sub_plan_version_id AS line_subplan_version_id,
                  p.current_version_id
           FROM sub_plans p
           LEFT JOIN sub_plan_versions v ON v.id=p.current_version_id
           LEFT JOIN master_plan_versions m ON m.id=p.master_plan_version_id
           LEFT JOIN production_lines pl ON pl.sub_plan_id=p.id
           LEFT JOIN production_line_versions plv ON plv.id=pl.current_version_id
           WHERE p.id='integration-sub'"""
    ).fetchone()
    assert state_after_rejection is not None, "rejected request removed the subplan"
    assert state_after_rejection["subplan_status"] == "active", dict(state_after_rejection)
    assert state_after_rejection["version_status"] == "effective", dict(state_after_rejection)
    assert state_after_rejection["master_status"] == "effective", dict(state_after_rejection)
    assert state_after_rejection["line_status"] == "active", dict(state_after_rejection)
    assert state_after_rejection["line_version_status"] == "effective", dict(state_after_rejection)
    assert state_after_rejection["line_subplan_version_id"] == state_after_rejection["current_version_id"], dict(state_after_rejection)

    created = tc.post("/api/subplans/integration-sub/rounds", json={"topic_count": 1})
    assert created.status_code == 201, created.text
    assert created.json()["topic_count"] == 1
    # main 合入后，round 创建不再预创建 production_tasks（deferred to executor）
    assert created.json()["task_creation"] == "deferred_to_round_executor"
    assert created.json()["production_task_count"] == 0
    round_id = int(created.json()["round_id"])
    assert int(conn.execute(
        "SELECT COUNT(*) FROM production_tasks WHERE round_id=?", (round_id,)
    ).fetchone()[0]) == 0

    binding_sql = (
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id,"
        "sub_plan_snapshot,plan_attribution_status,production_line_version_id "
        "FROM spiral_rounds WHERE id=?"
    )
    bound = conn.execute(binding_sql, (round_id,)).fetchone()
    assert int(bound["master_plan_version_id"]) == int(master["id"])
    assert bound["sub_plan_id"] == "integration-sub"
    assert int(bound["sub_plan_version_id"]) == int(version["id"])
    assert int(bound["sub_plan_stage_id"]) == int(stage["id"])
    assert bound["plan_attribution_status"] == "resolved"
    assert bound["production_line_version_id"] is not None
    assert json.loads(bound["sub_plan_snapshot"])["title"] == "联调子计划"
    assert created.json()["task_creation"] == "deferred_to_round_executor"
    assert created.json()["production_task_count"] == 0
    assert int(conn.execute(
        "SELECT COUNT(*) FROM production_tasks WHERE round_id=?", (round_id,)
    ).fetchone()[0]) == 0

    # Store-level audit records must retain enqueue-time initiator and actual worker identity.
    from src.core.content_production_line_store import ContentProductionLineStore
    from src.core.task_context import bind_task_identity
    # main 合入后 round 创建不再预创建 production_tasks，手动建一条来验证 audit 传递
    # 注意：production_tasks INSERT trigger (trg_content_production_line_after_task_insert)
    # 会自动创建 content_production_lines + line_created event，所以不需要手动插
    task_id = "audit-regression-task-001"
    plv_id = int(created.json()["bound_production_line_version_id"])
    conn.execute(
        """INSERT INTO production_tasks(
            id,idempotency_key,round_id,platform,task_ordinal,status,
            master_plan_version_id,sub_plan_id,sub_plan_version_id,strategy_step_id,
            production_line_version_id
        ) VALUES(?,?,?,?,1,'pending',?,?,?,?,?)""",
        (task_id, f"audit-{task_id}-{round_id}", round_id, "xhs", int(master["id"]), "integration-sub", int(version["id"]), str(stage["stage_key"]), plv_id),
    )
    line_id = f"cpl-{task_id}"
    # 验证 trigger 已自动创建 content_production_line 和 line_created event
    auto_line = conn.execute(
        "SELECT id,status FROM content_production_lines WHERE id=?", (line_id,)
    ).fetchone()
    assert auto_line is not None and auto_line["status"] == "pending", f"trigger 未自动创建 line: {auto_line}"
    conn.commit()
    with bind_task_identity(
        initiating_actor="session:0123456789abcdef0123",
        executing_actor="service:global-task-worker",
        tenant_id="tenant-integration",
        task_id="queue-task-123",
    ):
        ContentProductionLineStore(conn, tenant_id="tenant-integration").transition(
            line_id, event_type="aborted",
            actor="service:global-task-worker", reason="audit-context regression",
        )
    audit = conn.execute(
        """SELECT actor_type,actor_id,tenant_id,initiating_actor,executing_actor,task_id
           FROM content_production_line_events
           WHERE content_production_line_id=? AND event_type='aborted'""",
        (line_id,),
    ).fetchone()
    assert tuple(audit) == (
        "session", "0123456789abcdef0123", "tenant-integration",
        "session:0123456789abcdef0123", "service:global-task-worker", "queue-task-123",
    )

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
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=?", (int(version["id"]),)
    ).fetchone()
    from src.core.production_line_store import ProductionLineStore
    line_store = ProductionLineStore(conn)
    line_draft = line_store.create_draft(
        "completed-review-sub", int(version["id"]),
        {"title": "复核门禁测试生产线", "stages": [{
            "stage_key": str(stage["stage_key"]), "batch_size": 1,
            "production_goal": str(stage["title"]),
        }]},
        generation_source="manual", author="test",
    )
    line_store.activate(int(line_draft["id"]), reviewer="test")
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
    _activate_test_production_line(conn, "dependency-sub", int(version["id"]))
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
    _activate_test_production_line(conn, "entry-sub", int(version["id"]))

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



def test_manual_subplan_round_is_queued_without_placeholder_tasks(client, tmp_path, monkeypatch):
    tc, conn, _ = client
    _approved_subplan(conn, "manual-queue-sub")
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *args, **kwargs: {})
    api_main._g_ctx["runtime_tenant_id"] = "default"

    created = tc.post("/api/subplans/manual-queue-sub/rounds", json={"topic_count": 1})
    assert created.status_code == 201
    round_id = int(created.json()["round_id"])
    assert int(conn.execute(
        "SELECT COUNT(*) FROM production_tasks WHERE round_id=?", (round_id,)
    ).fetchone()[0]) == 0

    submitted = tc.post(
        f"/api/rounds/{round_id}/run",
        json={"topic_count": 1, "platforms": ["xhs"]},
    )
    assert submitted.status_code == 202
    task = queue.get(submitted.json()["task_id"])
    assert task is not None and task.status == "queued"
    assert task.task_type == "round_run"
    assert task.aggregate_id == str(round_id)
    assert int(conn.execute(
        "SELECT COUNT(*) FROM production_tasks WHERE round_id=?", (round_id,)
    ).fetchone()[0]) == 0


def test_condition_review_api_records_business_gate_decision(client):
    tc, conn, _ = client
    _store, _master, _version, stage = _approved_subplan(conn, "condition-review-sub")
    conn.execute(
        "UPDATE sub_plan_stages SET business_completion_required=1 WHERE id=?",
        (int(stage["id"]),),
    )
    conn.commit()

    # needs_review is a valid human decision without pretending that the publish
    # quantity target has already been met; the stats relation is a read-only view.
    response = tc.post(
        f"/api/subplans/condition-review-sub/stages/{int(stage['id'])}/condition-review",
        json={
            "condition_type": "business", "decision": "needs_review",
            "reason": "需要补充业务证据后再确认",
            "evidence": {"source": "acceptance-test"},
        },
    )
    assert response.status_code == 200
    assert response.json()["stage"]["business_completion_status"] == "needs_review"
    history = tc.get(
        f"/api/subplans/condition-review-sub/stages/{int(stage['id'])}/condition-reviews"
    )
    assert history.status_code == 200
    assert history.json()["condition_reviews"][0]["decision"] == "needs_review"



@pytest.mark.asyncio
async def test_ai_subplan_description_does_not_enable_business_or_effect_gates_by_default(
    client, monkeypatch
):
    from src.api import subplans as subplans_api

    tc, conn, _seed = client
    if conn.execute("SELECT 1 FROM account_group_north_star_versions WHERE status='effective' LIMIT 1").fetchone() is None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO account_group_north_star_versions(
                version_no,snapshot_json,status,change_reason,created_by,created_at,effective_at
            ) VALUES(1,?,'effective','AI subplan test prerequisite','test',?,?)""",
            (json.dumps({
                "positioning": "测试定位", "business_direction": "测试方向",
                "target_users": "测试用户", "core_value": "测试价值",
                "long_term_content_direction": "测试内容方向", "business_model": "测试模式",
                "platform_relationships": "", "long_term_goals": "测试目标",
            }, ensure_ascii=False), now, now),
        )
        conn.commit()
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "subplans": [{
                    "platform": "xhs",
                    "title": "AI 默认门禁测试",
                    "snapshot": {},
                    "stages": [{
                        "stage_key": "ai-default-gates",
                        "title": "阶段一",
                        "target_count": 1,
                        "completion_criteria": "完成目标内容生产",
                        "effect_evaluation": "观察发布效果并总结",
                    }],
                }],
            }, ensure_ascii=False)}}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(subplans_api.httpx, "AsyncClient", FakeAsyncClient)
    cfg = api_main._g_ctx["cfg"]
    cfg.llm.base_url = "https://llm.example/v1"
    cfg.llm.model = "test-model"
    cfg.llm.api_key_env = "T10_TEST_LLM_KEY"
    monkeypatch.setenv("T10_TEST_LLM_KEY", "test-key")

    response = tc.post(
        "/api/subplans/ai-draft",
        json={"master_plan_version_id": int(master["id"]), "platforms": ["xhs"]},
    )
    assert response.status_code == 201, response.text
    stage = conn.execute(
        """SELECT business_completion_required,effect_required
           FROM sub_plan_stages WHERE sub_plan_version_id=?""",
        (int(response.json()["items"][0]["draft"]["id"]),),
    ).fetchone()
    assert int(stage["business_completion_required"]) == 0
    assert int(stage["effect_required"]) == 0


def test_subplan_round_worker_persists_task_content_and_frozen_plan_lineage(
    client, cfg, tmp_path, monkeypatch,
):
    """A real sub-plan round must create its production task and attributable content in Worker."""
    from pathlib import Path

    from src.core import round_executor as executor_module
    from src.core.round_executor import RoundExecutor
    from src.core.production_task_store import ProductionTaskStore
    from src.core.guard.ultimate_goal_checker import CheckOutcome, CheckResult

    tc, conn, _seed = client
    saved_tenant = api_main._g_ctx.get("runtime_tenant_id")
    api_main._g_ctx["runtime_tenant_id"] = "default"
    store, master, version, stage = _approved_subplan(conn, "worker-provenance-sub")
    # Candidate asset metadata can contain arbitrary user-supplied values. The
    # immutable task snapshot keeps only the approved, minimum provenance fields.
    asset = tc.post("/api/original-assets", json={
        "asset_type": "product_image", "title": "Worker provenance approved asset",
        "description": "Approved reference candidate for the worker snapshot test",
        "source_kind": "manual", "source_version": "fixture-v1",
        "metadata": {"media_type": "image", "private_contact": "must-not-enter-task-snapshot"},
    })
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["id"]
    reviewed_asset = tc.post(f"/api/original-assets/{asset_id}/review", json={
        "decision": "confirmed", "review_note": "许可与隐私已核验",
        "quality": {
            "commercial_use_status": "allowed", "privacy_status": "clear",
            "readability_status": "readable", "completeness_status": "complete",
            "duplicate_status": "unique",
        },
    })
    assert reviewed_asset.status_code == 200, reviewed_asset.text
    linked_asset = tc.post(f"/api/original-assets/{asset_id}/links", json={
        "entity_type": "plan", "entity_id": "worker-provenance-sub",
        "relation_type": "reference_candidate",
    })
    assert linked_asset.status_code == 201, linked_asset.text
    # The generated content must flow through the real effect-review gate as well.
    conn.execute(
        "UPDATE sub_plan_stages SET effect_required=1,effect_status='pending' WHERE id=?",
        (int(stage["id"]),),
    )
    conn.commit()
    opened = tc.post(
        "/api/subplans/worker-provenance-sub/rounds", json={"topic_count": 1},
    )
    assert opened.status_code == 201, opened.text
    round_id = int(opened.json()["round_id"])
    round_row = conn.execute(
        """SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,
                  sub_plan_stage_id,sub_plan_snapshot,sub_plan_stage_snapshot,
                  production_line_version_id,plan_signature
           FROM spiral_rounds WHERE id=?""",
        (round_id,),
    ).fetchone()
    assert round_row is not None
    assert int(round_row["master_plan_version_id"]) == int(master["id"])
    assert int(round_row["sub_plan_version_id"]) == int(version["id"])
    assert int(round_row["sub_plan_stage_id"]) == int(stage["id"])
    assert round_row["production_line_version_id"] is not None

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    saved_queue = api_main._task_queue
    api_main._task_queue = queue
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *a, **k: {})
    from src.core import task_snapshot
    monkeypatch.setattr(task_snapshot, "validate_execution_snapshot", lambda *a, **k: None)

    worker_cfg = cfg.model_copy(update={
        "paths": cfg.paths.model_copy(update={
            "data_dir": str(tmp_path / "tenant" / "data"),
            "output_dir": str(tmp_path / "tenant" / "output"),
        }),
    })
    topic = SimpleNamespace(
        id="t8b-subplan-worker-topic", title="子计划真实 Worker 归属验收",
        summary="验证真实执行器创建生产任务并固化版本归属",
        dimension="AI教育", source="test", date="2026-11-05",
    )
    scored = SimpleNamespace(
        topic=topic, total_score=91.0, score_breakdown={}, is_exploration=False,
    )

    class PassingGuard:
        def check_content(self, *_args, **_kwargs):
            return CheckOutcome(CheckResult.PASS, "deterministic test pass")

    def fake_generate_card_set(*, output_dir, **_kwargs):
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        image = path / "t8b-subplan-worker-card.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"deterministic-image")
        return [str(image)], []

    monkeypatch.setattr(executor_module, "load_pool", lambda *_: ([topic], {}))
    monkeypatch.setattr(executor_module, "_llm_available", lambda _cfg: True)
    monkeypatch.setattr(
        executor_module, "generate_copy_content",
        lambda *_args, **_kwargs: (
            "子计划真实执行器标题", "用于验证生产任务与计划版本完整归属的图文正文。", ["AI教育"],
        ),
    )
    monkeypatch.setattr(
        executor_module.PlanVersionManager, "load_plan",
        lambda _self: (
            dict(PLAN_BASE), "fallback-signature", None, "plan_settings",
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

    try:
        submitted = tc.post(
            f"/api/rounds/{round_id}/run",
            json={"topic_count": 1, "platforms": ["xhs"], "save_drafts": False},
        )
        assert submitted.status_code == 202, submitted.text
        queue_task_id = submitted.json()["task_id"]
        queued = queue.get(queue_task_id)
        assert queued is not None and queued.status == "queued"
        assert queued.tenant_id == "default"

        stop = asyncio.Event()

        class StopAfterOneWorker(GlobalTaskWorker):
            async def _execute(self, task, **kwargs):
                await super()._execute(task, **kwargs)
                stop.set()

        worker = StopAfterOneWorker(
            queue, build_tenant_context, poll_interval_seconds=0.01,
            lease_seconds=30, heartbeat_seconds=1, reconcile_terminal_rounds=False,
        )
        asyncio.run(worker.run(stop))

        completed = queue.get(queue_task_id)
        assert completed is not None and completed.status == "succeeded"
        tasks = ProductionTaskStore(conn).list_tasks(round_id=round_id)
        assert len(tasks) == 1
        production_task = tasks[0]
        assert production_task["status"] == "succeeded"
        assert production_task["content_id"]
        assert production_task["master_plan_version_id"] == int(master["id"])
        assert production_task["sub_plan_id"] == "worker-provenance-sub"
        assert production_task["sub_plan_version_id"] == int(version["id"])
        assert production_task["strategy_step_id"] == stage["stage_key"]
        assert production_task["production_line_version_id"] == round_row["production_line_version_id"]
        asset_candidates = production_task["input_snapshot"]["task_input"]["original_assets"]
        assert len(asset_candidates) == 1
        candidate = asset_candidates[0]
        assert candidate["asset_id"] == asset_id
        assert candidate["version_no"] == reviewed_asset.json()["version_no"]
        assert candidate["commercial_use_status"] == "allowed"
        assert candidate["privacy_status"] == "clear"
        assert "metadata" not in candidate
        assert "private_contact" not in json.dumps(production_task["input_snapshot"], ensure_ascii=False)
        # A linked candidate is not proof of actual consumption; it must not be
        # recorded as used until an explicit task-assets authorization is made.
        assert conn.execute(
            "SELECT COUNT(*) FROM production_task_asset_usages WHERE production_task_id=?",
            (production_task["id"],),
        ).fetchone()[0] == 0
        frozen_round = production_task["input_snapshot"]["round"]
        assert frozen_round["master_plan_version_id"] == int(master["id"])
        assert frozen_round["sub_plan_id"] == "worker-provenance-sub"
        assert frozen_round["sub_plan_version_id"] == int(version["id"])
        assert frozen_round["sub_plan_stage_id"] == int(stage["id"])
        assert frozen_round["production_line_version_id"] == round_row["production_line_version_id"]

        content = conn.execute(
            """SELECT id,production_task_id,round_id,title
               FROM contents WHERE production_task_id=?""",
            (production_task["id"],),
        ).fetchone()
        assert content is not None
        assert content["id"] == production_task["content_id"]
        assert int(content["round_id"]) == round_id
        assert content["title"] == "子计划真实执行器标题"
        draft = conn.execute(
            "SELECT id,status FROM drafts WHERE content_id=?", (content["id"],),
        ).fetchone()
        assert draft is not None and draft["status"] == "draft_pending"

        # Continue with the actual content lifecycle APIs; no SQL state changes are
        # used for review, publication, metrics, or effect feedback.
        approved = tc.post(
            f"/api/contents/{content['id']}/approve",
            json={"reason": "真实 Worker 成品进入 T8-B 审核验收"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["review_status"] == "approved"

        published = tc.post(
            f"/api/drafts/{draft['id']}/publish_confirm",
            json={"note_url": "https://www.xiaohongshu.com/explore/t8b-worker-provenance"},
        )
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "user_published"

        metrics = tc.post(
            f"/api/drafts/{draft['id']}/metrics/manual",
            json={"views": 120, "likes": 12, "collects": 8, "comments": 3},
        )
        assert metrics.status_code == 200, metrics.text
        metric_rows = tc.get(
            "/api/metrics/snapshots", params={"draft_id": draft["id"]},
        )
        assert metric_rows.status_code == 200
        assert metric_rows.json()["total"] == 1
        assert metric_rows.json()["items"][0]["content_id"] == content["id"]

        effect = tc.post(
            f"/api/subplans/stages/{int(stage['id'])}/effect-feedback",
            json={
                "assessment": "effective",
                "recommendation": "保留该选题方向并继续观察。",
                "evidence_note": "来自真实 Worker 成品、人工审核、发布确认和指标回填。",
                "confidence": "medium",
            },
        )
        assert effect.status_code == 201, effect.text
        decision = tc.post(
            f"/api/subplans/worker-provenance-sub/stages/{int(stage['id'])}/condition-review",
            json={
                "condition_type": "effect",
                "decision": "passed",
                "reason": "核验真实发布成品的效果数据",
                "effect_review_id": effect.json()["id"],
            },
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["stage"]["effect_status"] == "passed"
    finally:
        api_main._task_queue = saved_queue
        if saved_tenant is None:
            api_main._g_ctx.pop("runtime_tenant_id", None)
        else:
            api_main._g_ctx["runtime_tenant_id"] = saved_tenant



def test_reviewed_feedback_adoption_drives_next_round_on_new_version(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    master = store.activate_master_version(int(master["id"]), reviewer="test")
    sub_id = "feedback-adoption-next-round"
    store.create_subplan(
        sub_id, master_plan_version_id=int(master["id"]), platform="xhs",
        title="反馈采纳后续轮次验收", author="test",
    )
    original = store.create_subplan_version(
        sub_id, {"title": "反馈采纳后续轮次验收", "platform": "xhs"},
        master_plan_version_id=int(master["id"]), author="test",
        stages=[{
            "stage_key": "feedback-adoption-stage", "title": "采纳后执行阶段",
            "target_count": 1, "effect_required": True,
        }],
    )
    store.activate_subplan_version(int(original["id"]), reviewer="test")
    original_stage = conn.execute(
        "SELECT id,stage_key FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (int(original["id"]),),
    ).fetchone()

    line = tc.post(f"/api/subplans/{sub_id}/production-line/draft", json={
        "definition": {
            "title": "原始策略生产线",
            "stages": [{
                "stage_key": original_stage["stage_key"], "batch_size": 1,
                "production_goal": "按原策略生产一条内容",
            }],
        },
    })
    assert line.status_code == 201, line.text
    old_line = line.json()["draft"]
    activated_line = tc.post(
        f"/api/production-lines/{old_line['production_line_id']}/versions/{old_line['id']}/review",
        json={"action": "approve"},
    )
    assert activated_line.status_code == 200, activated_line.text

    feedback = tc.post(
        f"/api/subplans/stages/{int(original_stage['id'])}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "保持高表现策略并扩大样本。",
            "evidence_note": "后续轮次版本绑定验收。",
            "confidence": "high",
        },
    )
    assert feedback.status_code == 201, feedback.text
    reviewed = tc.post(
        f"/api/subplans/{sub_id}/stages/{int(original_stage['id'])}/condition-review",
        json={
            "condition_type": "effect", "decision": "passed",
            "reason": "人工确认效果反馈", "effect_review_id": feedback.json()["id"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text

    adopted = tc.post("/api/plan_adjustments/adopt", json={
        "dimensions": ["发布节奏"], "effect_review_id": feedback.json()["id"],
    })
    assert adopted.status_code == 200, adopted.text
    new_version = adopted.json()["drafts"][0]["draft_version"]
    assert new_version["status"] == "draft"
    approved = tc.post(f"/api/subplans/{sub_id}/review", json={
        "version_id": int(new_version["id"]), "action": "approve",
    })
    assert approved.status_code == 200, approved.text

    new_stage = conn.execute(
        "SELECT id,stage_key FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order LIMIT 1",
        (int(new_version["id"]),),
    ).fetchone()
    assert new_stage["stage_key"] == original_stage["stage_key"]
    revised_line = tc.post(f"/api/subplans/{sub_id}/production-line/draft", json={
        "definition": {
            "title": "采纳后策略生产线",
            "stages": [{
                "stage_key": new_stage["stage_key"], "batch_size": 1,
                "production_goal": "按已审核采纳策略生产一条内容",
            }],
        },
        "change_note": "同步已审核采纳的子计划版本",
    })
    assert revised_line.status_code == 201, revised_line.text
    new_line = revised_line.json()["draft"]
    line_review = tc.post(
        f"/api/production-lines/{new_line['production_line_id']}/versions/{new_line['id']}/review",
        json={"action": "approve"},
    )
    assert line_review.status_code == 200, line_review.text

    conn.execute(
        "UPDATE spiral_rounds SET status='completed' WHERE status IN "
        "('created','producing','awaiting_publish','collecting_metrics','analyzing')"
    )
    conn.commit()
    next_round = tc.post(f"/api/subplans/{sub_id}/rounds", json={"topic_count": 1})
    assert next_round.status_code == 201, next_round.text
    round_id = int(next_round.json()["round_id"])
    binding = conn.execute(
        "SELECT sub_plan_version_id,sub_plan_stage_id,production_line_version_id "
        "FROM spiral_rounds WHERE id=?", (round_id,),
    ).fetchone()
    assert int(binding["sub_plan_version_id"]) == int(new_version["id"])
    assert int(binding["sub_plan_stage_id"]) == int(new_stage["id"])
    assert int(binding["production_line_version_id"]) == int(new_line["id"])
    assert int(binding["sub_plan_version_id"]) != int(original["id"])



def test_effect_feedback_is_idempotent_and_rejects_tenant_mismatch(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    store.create_subplan(
        "effect-feedback-idempotency", master_plan_version_id=int(master["id"]),
        platform="xhs", title="效果反馈幂等验收", author="test",
    )
    version = store.create_subplan_version(
        "effect-feedback-idempotency", {"title": "效果反馈幂等验收", "platform": "xhs"},
        master_plan_version_id=int(master["id"]), author="test",
        stages=[{"stage_key": "idempotency-stage", "title": "幂等阶段", "target_count": 1}],
    )
    store.activate_subplan_version(int(version["id"]), reviewer="test")
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (int(version["id"]),)
    ).fetchone()
    url = f"/api/subplans/stages/{int(stage['id'])}/effect-feedback"
    payload = {
        "assessment": "effective",
        "recommendation": "保持当前策略并扩大样本。",
        "evidence_note": "重复请求应复用同一条不可变反馈。",
        "confidence": "high",
    }
    before = int(conn.execute(
        "SELECT COUNT(*) FROM strategy_effect_reviews WHERE stage_id=?", (int(stage["id"]),)
    ).fetchone()[0])

    cross_tenant = tc.post(url, headers={"X-Tenant-Id": "tenant-other"}, json=payload)
    assert cross_tenant.status_code == 409, cross_tenant.text
    assert int(conn.execute(
        "SELECT COUNT(*) FROM strategy_effect_reviews WHERE stage_id=?", (int(stage["id"]),)
    ).fetchone()[0]) == before

    headers = {"Idempotency-Key": "effect-feedback-idempotency-key-001"}
    first = tc.post(url, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    replay = tc.post(url, headers=headers, json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["deduplicated"] is True

    changed_payload = {**payload, "recommendation": "相同幂等键不得覆盖原反馈。"}
    conflict = tc.post(url, headers=headers, json=changed_payload)
    assert conflict.status_code == 409, conflict.text
    assert int(conn.execute(
        "SELECT COUNT(*) FROM strategy_effect_reviews WHERE stage_id=?", (int(stage["id"]),)
    ).fetchone()[0]) == before + 1



def test_feedback_adoption_is_scoped_to_its_exact_source_subplan(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(master["id"]), reviewer="test")
    versions = {}
    for sub_id, platform in (("feedback-source-xhs", "xhs"), ("feedback-unrelated-douyin", "douyin")):
        store.create_subplan(
            sub_id, master_plan_version_id=int(master["id"]), platform=platform,
            title="反馈来源隔离验收", author="test",
        )
        version = store.create_subplan_version(
            sub_id, {"title": "反馈来源隔离验收", "platform": platform},
            master_plan_version_id=int(master["id"]), author="test",
            stages=[{"stage_key": "same-key", "title": "同名阶段", "target_count": 1}],
        )
        store.activate_subplan_version(int(version["id"]), reviewer="test")
        versions[sub_id] = int(version["id"])

    source_stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (versions["feedback-source-xhs"],),
    ).fetchone()
    feedback = tc.post(
        f"/api/subplans/stages/{int(source_stage['id'])}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "仅调整反馈来源子计划。",
            "evidence_note": "同 stage_key 的其他子计划不得被采纳。",
            "confidence": "high",
        },
    )
    assert feedback.status_code == 201, feedback.text

    adopted = tc.post("/api/plan_adjustments/adopt", json={
        "dimensions": ["内容结构"], "effect_review_id": feedback.json()["id"],
    })
    assert adopted.status_code == 200, adopted.text
    drafts = adopted.json()["drafts"]
    assert len(drafts) == 1
    assert drafts[0]["sub_plan_id"] == "feedback-source-xhs"
    first_draft_id = int(drafts[0]["draft_version"]["id"])

    # A retry after a lost response must reuse the same pending draft, not
    # increment the sub-plan version number or create another review item.
    replay = tc.post("/api/plan_adjustments/adopt", json={
        "dimensions": ["内容结构"], "effect_review_id": feedback.json()["id"],
    })
    assert replay.status_code == 200, replay.text
    assert len(replay.json()["drafts"]) == 1
    assert int(replay.json()["drafts"][0]["draft_version"]["id"]) == first_draft_id
    assert replay.json()["drafts"][0]["reused"] is True
    assert int(conn.execute(
        "SELECT COUNT(*) FROM sub_plan_versions WHERE sub_plan_id=? AND status='draft'",
        ("feedback-source-xhs",),
    ).fetchone()[0]) == 1

    unrelated = conn.execute(
        "SELECT current_version_id,status FROM sub_plans WHERE id='feedback-unrelated-douyin'"
    ).fetchone()
    assert int(unrelated["current_version_id"]) == versions["feedback-unrelated-douyin"]
    assert unrelated["status"] == "active"
    source = conn.execute(
        "SELECT current_version_id,status FROM sub_plans WHERE id='feedback-source-xhs'"
    ).fetchone()
    assert int(source["current_version_id"]) == versions["feedback-source-xhs"]
    assert source["status"] == "active"



def test_master_adoption_rejects_effect_feedback_from_superseded_master_version(client):
    tc, conn, _ = client
    store = PlanVersionStore(conn)
    first_master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(first_master["id"]), reviewer="test")
    store.create_subplan(
        "superseded-feedback-source", master_plan_version_id=int(first_master["id"]),
        platform="xhs", title="跨母计划版本反馈验收", author="test",
    )
    child = store.create_subplan_version(
        "superseded-feedback-source", {"title": "跨母计划版本反馈验收", "platform": "xhs"},
        master_plan_version_id=int(first_master["id"]), author="test",
        stages=[{"stage_key": "old-master-stage", "title": "旧母计划阶段", "target_count": 1}],
    )
    store.activate_subplan_version(int(child["id"]), reviewer="test")
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (int(child["id"]),)
    ).fetchone()
    feedback = tc.post(
        f"/api/subplans/stages/{int(stage['id'])}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "旧母计划反馈不得跨版本写入新母计划。",
            "evidence_note": "跨版本负向验收。",
            "confidence": "high",
        },
    )
    assert feedback.status_code == 201, feedback.text

    second_master = store.create_master_draft(dict(PLAN_BASE), author="test", source="user")
    store.activate_master_version(int(second_master["id"]), reviewer="test")
    drafts_before = int(conn.execute(
        "SELECT COUNT(*) FROM master_plan_versions WHERE status='draft'"
    ).fetchone()[0])
    rejected = tc.post("/api/plan_adjustments/adopt_master", json={
        "dimensions": ["内容结构"], "effect_review_id": feedback.json()["id"],
    })
    assert rejected.status_code == 409, rejected.text
    assert int(conn.execute(
        "SELECT COUNT(*) FROM master_plan_versions WHERE status='draft'"
    ).fetchone()[0]) == drafts_before



def test_effect_condition_review_persists_status_and_auditable_decision(client):
    """通过效果审核必须更新当前阶段门状态并保留双重审计记录。"""
    tc, conn, _ = client
    _store, _master, version, stage = _approved_subplan(
        conn, sub_id="effect-review-status-sub",
    )
    conn.execute(
        "UPDATE sub_plan_stages SET effect_required=1 WHERE id=?",
        (int(stage["id"]),),
    )
    conn.commit()

    response = tc.post(
        f"/api/subplans/effect-review-status-sub/stages/{int(stage['id'])}/condition-review",
        json={
            "condition_type": "effect",
            "decision": "passed",
            "reason": "效果证据达到阶段要求",
            "evidence": {"source": "integration-regression", "sample_count": 12},
            "recommendation": "沿用当前策略",
            "confidence": "high",
        },
    )

    assert response.status_code == 200, response.text
    persisted = conn.execute(
        "SELECT effect_status FROM sub_plan_stages WHERE id=?",
        (int(stage["id"]),),
    ).fetchone()
    assert persisted["effect_status"] == "passed"
    effect_review = conn.execute(
        "SELECT assessment,recommendation,confidence,evidence_json FROM strategy_effect_reviews WHERE stage_id=?",
        (int(stage["id"]),),
    ).fetchone()
    assert effect_review["assessment"] == "effective"
    assert effect_review["recommendation"] == "沿用当前策略"
    assert effect_review["confidence"] == "high"
    assert '"sample_count": 12' in effect_review["evidence_json"]
    condition_review = conn.execute(
        "SELECT condition_type,decision,reason FROM strategy_step_condition_reviews WHERE stage_id=?",
        (int(stage["id"]),),
    ).fetchone()
    assert tuple(condition_review) == ("effect", "passed", "效果证据达到阶段要求")


def test_subplan_revision_preserves_unchanged_stage_gate_state(client):
    """修订阶段描述但门禁契约不变时，继承审核状态且不信任客户端状态字段。"""
    _tc, conn, _ = client
    store, master, version, stage = _approved_subplan(
        conn, sub_id="preserve-stage-gates-sub",
    )
    conn.execute(
        """UPDATE sub_plan_stages SET status='completed',
               entry_condition='已满足进入条件',entry_condition_met=1,
               completion_criteria='发布数量达到目标',
               business_completion_required=1,business_completion_status='passed',
               effect_evaluation='互动率达到目标',
               effect_required=1,effect_status='passed'
           WHERE id=?""",
        (int(stage["id"]),),
    )
    conn.commit()

    base_stage = dict(conn.execute(
        "SELECT * FROM sub_plan_stages WHERE id=?", (int(stage["id"]),)
    ).fetchone())
    for field in ("id", "sub_plan_version_id", "created_at", "updated_at"):
        base_stage.pop(field, None)
    # Simulate a stale/malicious client payload: lifecycle fields must be sourced
    # from the immutable base version, never accepted from the request body.
    base_stage["status"] = "pending"
    base_stage["entry_condition_met"] = False
    base_stage["business_completion_status"] = "pending"
    base_stage["effect_status"] = "pending"
    snapshot = json.loads(version["snapshot_json"])
    snapshot["description"] = "只调整说明，不改变阶段门禁契约"

    revision = store.create_subplan_version(
        "preserve-stage-gates-sub", snapshot,
        master_plan_version_id=int(master["id"]),
        stages=[base_stage],
        based_on_version_id=int(version["id"]),
        author="test",
        change_note="更新说明",
    )
    revised_stage = conn.execute(
        "SELECT status,entry_condition_met,business_completion_status,effect_status "
        "FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (int(revision["id"]),),
    ).fetchone()

    assert tuple(revised_stage) == ("completed", 1, "passed", "passed")
