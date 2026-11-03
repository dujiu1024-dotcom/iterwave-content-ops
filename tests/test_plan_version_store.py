import json
import sqlite3

import pytest

from src.core.plan_version_store import PlanVersionError, PlanVersionStore


def _db(path=None):
    conn = sqlite3.connect(str(path) if path is not None else ":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE plan_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), plan_snapshot TEXT NOT NULL,
            plan_signature TEXT NOT NULL, updated_at TEXT, updated_by TEXT,
            active_version_id INTEGER
        );
        CREATE TABLE signature_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, plan_signature TEXT NOT NULL,
            plan_snapshot TEXT NOT NULL, round_id INTEGER, created_at TEXT, author TEXT
        );
        CREATE TABLE master_plan_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, version_no INTEGER NOT NULL UNIQUE,
            plan_signature TEXT NOT NULL, plan_snapshot TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','effective','archived')),
            source TEXT NOT NULL, legacy_history_id INTEGER UNIQUE,
            based_on_version_id INTEGER, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT,
            effective_at TEXT, superseded_at TEXT, change_note TEXT,
            deleted_at TEXT, deleted_by TEXT
        );
        CREATE TABLE sub_plans (
            id TEXT PRIMARY KEY, master_plan_version_id INTEGER NOT NULL,
            platform TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
            current_version_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL, archived_at TEXT,
            pause_reason TEXT, paused_by_master_version_id INTEGER
        );
        CREATE TABLE sub_plan_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sub_plan_id TEXT NOT NULL,
            version_no INTEGER NOT NULL, master_plan_version_id INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL, status TEXT NOT NULL, based_on_version_id INTEGER,
            created_at TEXT NOT NULL, created_by TEXT NOT NULL, reviewed_at TEXT,
            reviewed_by TEXT, review_note TEXT, effective_at TEXT, superseded_at TEXT,
            change_note TEXT, UNIQUE(sub_plan_id,version_no)
        );
        CREATE TABLE sub_plan_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sub_plan_version_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL, stage_order INTEGER NOT NULL, title TEXT NOT NULL, direction TEXT NOT NULL,
            target_count INTEGER NOT NULL, start_date TEXT, end_date TEXT,
            completion_condition TEXT NOT NULL, status TEXT NOT NULL, notes TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            strategy_goal TEXT NOT NULL DEFAULT '', current_problem TEXT NOT NULL DEFAULT '',
            execution_strategy TEXT NOT NULL DEFAULT '', prerequisites TEXT NOT NULL DEFAULT '',
            input_info TEXT NOT NULL DEFAULT '', expected_result TEXT NOT NULL DEFAULT '',
            completion_criteria TEXT NOT NULL DEFAULT '', effect_evaluation TEXT NOT NULL DEFAULT '',
            entry_condition TEXT NOT NULL DEFAULT '', entry_condition_met INTEGER NOT NULL DEFAULT 1,
            dependencies_json TEXT NOT NULL DEFAULT '[]',
            business_completion_required INTEGER NOT NULL DEFAULT 0,
            business_completion_status TEXT NOT NULL DEFAULT 'not_required',
            effect_required INTEGER NOT NULL DEFAULT 0,
            effect_status TEXT NOT NULL DEFAULT 'not_required'
        );
        CREATE TABLE sub_plan_stage_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sub_plan_id TEXT NOT NULL,
            stage_key TEXT NOT NULL, source_version_id INTEGER NOT NULL,
            stage_id INTEGER NOT NULL, result_json TEXT NOT NULL,
            evaluation TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL DEFAULT 'user'
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, goal_id TEXT, round_number INTEGER, status TEXT,
            topic_ids TEXT, content_variants TEXT, trigger_source TEXT, applied_feedback TEXT,
            master_plan_version_id INTEGER, sub_plan_id TEXT, sub_plan_version_id INTEGER,
            sub_plan_stage_id INTEGER, sub_plan_snapshot TEXT, sub_plan_stage_snapshot TEXT,
            plan_signature TEXT, plan_attribution_status TEXT
        );
        CREATE TABLE round_open_idempotency (
            idempotency_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
            master_plan_version_id INTEGER NOT NULL, sub_plan_id TEXT NOT NULL,
            sub_plan_version_id INTEGER NOT NULL, sub_plan_stage_id INTEGER NOT NULL,
            status TEXT NOT NULL, round_id INTEGER UNIQUE, lease_until TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error_message TEXT
        );
        CREATE TABLE contents(id TEXT PRIMARY KEY, round_id INTEGER);
        CREATE TABLE drafts(id TEXT PRIMARY KEY, content_id TEXT, user_confirmed_at TEXT);
        CREATE TABLE user_override(
            id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER,
            override_field TEXT, original_value TEXT, override_value TEXT,
            reason TEXT NOT NULL, overridden_by TEXT NOT NULL, overridden_at TEXT
        );
        CREATE VIEW sub_plan_stage_publish_stats AS
        SELECT current_stage.id AS stage_id,current_stage.sub_plan_version_id,
               COUNT(DISTINCT d.id) AS confirmed_published_count
        FROM sub_plan_stages current_stage
        LEFT JOIN sub_plan_stages historical_stage
            ON historical_stage.stage_key=current_stage.stage_key
        LEFT JOIN spiral_rounds r ON r.sub_plan_stage_id=historical_stage.id
            AND r.sub_plan_version_id=historical_stage.sub_plan_version_id
        LEFT JOIN contents c ON c.round_id=r.id
        LEFT JOIN drafts d ON d.content_id=c.id AND d.user_confirmed_at IS NOT NULL
        GROUP BY current_stage.id,current_stage.sub_plan_version_id;
        INSERT INTO spiral_rounds(id,round_number,status,plan_attribution_status)
        VALUES(1,1,'created','pending');
    """)
    return conn


def _plan(count=5):
    return {
        "topic_count_per_platform": count,
        "dimension_allocation": {"AI教育": count},
        "enabled_platforms": ["xhs"],
        "content_format": "image_text",
    }


def _active_master(store):
    row = store.create_master_draft(_plan())
    store.activate_master_version(row["id"], reviewer="reviewer")
    return row


def _active_subplan(store, master):
    store.create_subplan(
        "sub-xhs", master_plan_version_id=master["id"],
        platform="xhs", title="小红书子计划",
    )
    version = store.create_subplan_version(
        "sub-xhs", {"title": "小红书子计划", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[{"title": "第一阶段", "target_count": 8, "direction": "AI教育"}],
    )
    store.activate_subplan_version(version["id"], reviewer="reviewer")
    stage_id = store.conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()[0]
    return version, stage_id


def test_master_plan_draft_requires_explicit_activation_and_preserves_previous_version():
    conn = _db()
    store = PlanVersionStore(conn)
    first = store.create_master_draft(_plan(5))
    store.activate_master_version(first["id"], reviewer="reviewer-1")
    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])

    active = conn.execute(
        "SELECT id,plan_snapshot FROM master_plan_versions WHERE status='effective'"
    ).fetchone()
    assert active["id"] == first["id"]
    assert json.loads(active["plan_snapshot"])["topic_count_per_platform"] == 5
    assert conn.execute("SELECT active_version_id FROM plan_settings WHERE id=1").fetchone()[0] == first["id"]

    store.activate_master_version(second["id"], reviewer="reviewer-2")
    states = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM master_plan_versions")}
    assert states == {first["id"]: "archived", second["id"]: "effective"}
    assert conn.execute("SELECT active_version_id FROM plan_settings WHERE id=1").fetchone()[0] == second["id"]


def test_restore_historical_version_creates_new_draft_instead_of_reactivating_old_row():
    conn = _db()
    store = PlanVersionStore(conn)
    original = store.create_master_draft(_plan(5))
    store.activate_master_version(original["id"], reviewer="reviewer")
    restored = store.create_master_draft_from_version(original["id"])

    assert restored["status"] == "draft"
    assert restored["based_on_version_id"] == original["id"]
    assert restored["id"] != original["id"]
    assert conn.execute(
        "SELECT status FROM master_plan_versions WHERE id=?", (original["id"],)
    ).fetchone()[0] == "effective"


def test_subplan_stage_and_round_binding_are_version_specific():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)
    store.bind_round(
        1, master_plan_version_id=master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=subplan["id"],
        sub_plan_stage_id=stage_id,
    )

    round_row = conn.execute(
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id,"
        "plan_attribution_status,sub_plan_snapshot,sub_plan_stage_snapshot "
        "FROM spiral_rounds WHERE id=1"
    ).fetchone()
    assert tuple(round_row[:5]) == (master["id"], "sub-xhs", subplan["id"], stage_id, "resolved")
    assert json.loads(round_row["sub_plan_snapshot"])["title"] == "小红书子计划"
    assert json.loads(round_row["sub_plan_stage_snapshot"])["title"] == "第一阶段"


def test_round_cannot_bind_stage_from_another_subplan_version():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    first, stage_one = _active_subplan(store, master)
    second = store.create_subplan_version(
        "sub-xhs", {"title": "v2"}, master_plan_version_id=master["id"],
        stages=[{"title": "阶段 B"}], based_on_version_id=first["id"],
    )
    store.activate_subplan_version(second["id"], reviewer="reviewer")
    with pytest.raises(PlanVersionError, match="不属于"):
        store.bind_round(
            1, master_plan_version_id=master["id"],
            sub_plan_id="sub-xhs", sub_plan_version_id=second["id"],
            sub_plan_stage_id=stage_one,
        )


def test_stage_publish_stats_count_only_user_confirmed_drafts_and_stage_lifecycle():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)
    store.bind_round(
        1, master_plan_version_id=master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=subplan["id"],
        sub_plan_stage_id=stage_id,
    )
    conn.execute("INSERT INTO contents(id,round_id) VALUES('content-1',1)")
    conn.execute("INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-1','content-1',NULL)")
    conn.execute("INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-2','content-1','2026-01-04')")
    for index in range(2, 9):
        conn.execute(
            "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES(?,?,?)",
            (f"draft-{index + 1}", "content-1", f"2026-01-{index + 4:02d}"),
        )
    conn.commit()

    assert store.get_stage_publish_stats(stage_id)["confirmed_published_count"] == 8
    store.update_stage_status(stage_id, "in_progress")
    store.update_stage_status(stage_id, "completed")
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (stage_id,)
    ).fetchone()[0] == "completed"
    with pytest.raises(PlanVersionError, match="不允许"):
        store.update_stage_status(stage_id, "in_progress")


def test_durable_round_open_lock_is_idempotent_and_rejects_key_reuse():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)

    first = store.reserve_round_open(
        "auto:2026-11-02T10:00", "hash-a",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    duplicate = store.reserve_round_open(
        "auto:2026-11-02T10:00", "hash-a",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    assert first["acquired"] is True
    assert duplicate["acquired"] is False
    assert duplicate["status"] == "in_progress"

    with pytest.raises(PlanVersionError, match="不同开轮请求"):
        store.reserve_round_open(
            "auto:2026-11-02T10:00", "hash-b",
            master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
            sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
        )

    completed = store.create_round_once(
        "auto:2026-11-02T10:00", goal_id="goal-1",
        topic_ids=["topic-1"], content_variants=[{"platform": "xhs"}],
    )
    assert completed["status"] == "created"
    assert completed["deduped"] is False
    assert tuple(conn.execute(
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id "
        "FROM spiral_rounds WHERE id=?", (completed["round_id"],)
    ).fetchone()) == (master["id"], "sub-xhs", subplan["id"], stage_id)
    retry = store.reserve_round_open(
        "auto:2026-11-02T10:00", "hash-a",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    assert retry == {"acquired": False, "status": "created", "round_id": completed["round_id"]}



def test_strategy_steps_persist_dependencies_and_require_real_predecessor_outcome():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    store.create_subplan("sub-strategy", master_plan_version_id=master["id"],
                         platform="xhs", title="策略子计划")
    version = store.create_subplan_version(
        "sub-strategy", {"title": "策略子计划", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[
            {"stage_key": "research", "title": "调研验证", "target_count": 1,
             "strategy_goal": "验证用户需求", "current_problem": "缺少真实反馈",
             "execution_strategy": "访谈并发布验证内容", "prerequisites": "准备访谈提纲",
             "input_info": "母计划目标", "expected_result": "得到需求证据",
             "completion_criteria": "收集到有效反馈", "effect_evaluation": "有效反馈率",
             "entry_condition": "调研资源就绪"},
            {"stage_key": "scale", "title": "规模化", "target_count": 2,
             "dependencies": ["research"], "entry_condition_met": True},
        ],
    )
    rows = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order",
        (version["id"],),
    ).fetchall()
    assert rows[0]["strategy_goal"] == "验证用户需求"
    assert json.loads(rows[1]["dependencies_json"]) == ["research"]

    with pytest.raises(PlanVersionError, match="前置阶段"):
        store.assert_stage_ready(rows[1]["id"], version["id"])

    store.update_stage_status(rows[0]["id"], "in_progress")
    conn.execute("INSERT INTO contents(id,round_id) VALUES('research-content',1)")
    conn.execute(
        "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('research-published','research-content','2026-11-03')"
    )
    conn.execute(
        "UPDATE spiral_rounds SET sub_plan_id='sub-strategy',sub_plan_version_id=?,sub_plan_stage_id=? WHERE id=1",
        (version["id"], rows[0]["id"]),
    )
    conn.execute(
        "UPDATE sub_plan_stages SET business_completion_status='passed',effect_status='passed' WHERE id=?",
        (rows[0]["id"],),
    )
    conn.commit()
    store.update_stage_status(rows[0]["id"], "completed")
    store.record_stage_outcome(
        rows[0]["id"], version["id"], {"published_count": 1, "feedback": "用户明确提出需求"},
        evaluation="",
    )
    with pytest.raises(PlanVersionError, match="缺少效果评价"):
        store.assert_stage_ready(rows[1]["id"], version["id"])

    store.record_stage_outcome(
        rows[0]["id"], version["id"], {},
        evaluation="有效反馈率达到目标",
    )
    with pytest.raises(PlanVersionError, match="实际结果损坏或为空"):
        store.assert_stage_ready(rows[1]["id"], version["id"])

    outcome = store.record_stage_outcome(
        rows[0]["id"], version["id"], {"published_count": 1, "feedback": "用户明确提出需求"},
        evaluation="有效反馈率达到目标",
    )
    assert outcome["result"]["published_count"] == 1
    assert store.assert_stage_ready(rows[1]["id"], version["id"])["stage_key"] == "scale"

    # A later failed review must invalidate an already-recorded predecessor outcome.
    conn.execute(
        "UPDATE sub_plan_stages SET effect_status='failed' WHERE id=?", (rows[0]["id"],)
    )
    with pytest.raises(PlanVersionError, match="业务完成或效果条件已失效"):
        store.assert_stage_ready(rows[1]["id"], version["id"])
    conn.execute(
        "UPDATE sub_plan_stages SET effect_status='passed' WHERE id=?", (rows[0]["id"],)
    )
    conn.commit()

    store.activate_subplan_version(version["id"], reviewer="reviewer")
    lease = store.reserve_round_open(
        "strategy-follow-up", "strategy-hash",
        master_plan_version_id=master["id"], sub_plan_id="sub-strategy",
        sub_plan_version_id=version["id"], sub_plan_stage_id=rows[1]["id"],
    )
    assert lease["acquired"] is True
    created = store.create_round_once("strategy-follow-up")
    stage_snapshot = json.loads(conn.execute(
        "SELECT sub_plan_stage_snapshot FROM spiral_rounds WHERE id=?",
        (created["round_id"],),
    ).fetchone()[0])
    assert stage_snapshot["dependency_outcomes"]["research"]["result"]["published_count"] == 1
    assert stage_snapshot["dependency_outcomes"]["research"]["evaluation"] == "有效反馈率达到目标"


def test_strategy_step_blocks_unmet_entry_condition_and_rejects_dependency_cycles():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    store.create_subplan("sub-gated", master_plan_version_id=master["id"],
                         platform="xhs", title="条件子计划")
    with pytest.raises(PlanVersionError, match="循环"):
        store.create_subplan_version(
            "sub-gated", {"title": "条件子计划"},
            master_plan_version_id=master["id"],
            stages=[
                {"stage_key": "a", "title": "A", "dependencies": ["b"]},
                {"stage_key": "b", "title": "B", "dependencies": ["a"]},
            ],
        )
    version = store.create_subplan_version(
        "sub-gated", {"title": "条件子计划"},
        master_plan_version_id=master["id"],
        stages=[{"stage_key": "gated", "title": "受限步骤", "entry_condition_met": False}],
    )
    stage_id = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()[0]
    with pytest.raises(PlanVersionError, match="进入条件"):
        store.assert_stage_ready(stage_id, version["id"])


def test_plan_versions_are_isolated_by_tenant_database_file(tmp_path):
    tenant_a = _db(tmp_path / "tenant-a.db")
    tenant_b = _db(tmp_path / "tenant-b.db")
    store_a = PlanVersionStore(tenant_a)
    store_b = PlanVersionStore(tenant_b)

    a = store_a.create_master_draft(_plan(5), author="tenant-a")
    b = store_b.create_master_draft(_plan(9), author="tenant-b")
    assert a["id"] == b["id"]  # local integer IDs may overlap safely
    store_a.activate_master_version(a["id"], reviewer="tenant-a")
    store_b.activate_master_version(b["id"], reviewer="tenant-b")
    store_a.create_subplan("same-local-id", master_plan_version_id=a["id"],
                           platform="xhs", title="Tenant A 子计划")
    store_b.create_subplan("same-local-id", master_plan_version_id=b["id"],
                           platform="xhs", title="Tenant B 子计划")
    assert store_a.list_master_versions()[0]["created_by"] == "tenant-a"
    assert store_b.list_master_versions()[0]["created_by"] == "tenant-b"
    assert json.loads(store_a.list_master_versions()[0]["plan_snapshot"])["topic_count_per_platform"] == 5
    assert json.loads(store_b.list_master_versions()[0]["plan_snapshot"])["topic_count_per_platform"] == 9
    assert store_a.conn.execute("SELECT title FROM sub_plans").fetchone()[0] == "Tenant A 子计划"
    assert store_b.conn.execute("SELECT title FROM sub_plans").fetchone()[0] == "Tenant B 子计划"
    tenant_a.close()
    tenant_b.close()



def test_subplan_can_rebase_to_new_master_without_rewriting_old_rounds():
    conn = _db()
    store = PlanVersionStore(conn)
    first_master = _active_master(store)
    first_subplan, first_stage = _active_subplan(store, first_master)
    store.bind_round(
        1, master_plan_version_id=first_master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=first_subplan["id"],
        sub_plan_stage_id=first_stage,
    )
    old_snapshot = conn.execute(
        "SELECT sub_plan_snapshot,sub_plan_stage_snapshot FROM spiral_rounds WHERE id=1"
    ).fetchone()

    next_master = store.create_master_draft(
        _plan(7), source="review_adoption", based_on_version_id=first_master["id"],
        change_note="复盘采纳生成的新主计划草稿",
    )
    assert next_master["status"] == "draft"
    store.activate_master_version(next_master["id"], reviewer="reviewer-2")
    assert conn.execute(
        "SELECT status FROM sub_plans WHERE id='sub-xhs'"
    ).fetchone()[0] == "paused"

    next_subplan = store.create_subplan_version(
        "sub-xhs", {"title": "新主计划下的小红书子计划", "platform": "xhs"},
        master_plan_version_id=next_master["id"], based_on_version_id=first_subplan["id"],
        stages=[{"title": "新阶段", "target_count": 10}],
    )
    store.activate_subplan_version(next_subplan["id"], reviewer="reviewer-2")

    identity = conn.execute(
        "SELECT master_plan_version_id,current_version_id FROM sub_plans WHERE id='sub-xhs'"
    ).fetchone()
    assert tuple(identity) == (next_master["id"], next_subplan["id"])
    old_round = conn.execute(
        "SELECT master_plan_version_id,sub_plan_version_id,sub_plan_snapshot,sub_plan_stage_snapshot "
        "FROM spiral_rounds WHERE id=1"
    ).fetchone()
    assert old_round["master_plan_version_id"] == first_master["id"]
    assert old_round["sub_plan_version_id"] == first_subplan["id"]
    assert old_round["sub_plan_snapshot"] == old_snapshot["sub_plan_snapshot"]
    assert old_round["sub_plan_stage_snapshot"] == old_snapshot["sub_plan_stage_snapshot"]



def test_integrity_audit_detects_signature_drift_and_wrong_round_stage_link():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)
    store.bind_round(
        1, master_plan_version_id=master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=subplan["id"],
        sub_plan_stage_id=stage_id,
    )
    assert store.validate_integrity()["ok"] is True

    conn.execute(
        "UPDATE spiral_rounds SET sub_plan_stage_id=999,sub_plan_stage_snapshot='{}' WHERE id=1"
    )
    conn.execute(
        "UPDATE master_plan_versions SET plan_snapshot='{}' WHERE id=?", (master["id"],)
    )
    conn.commit()
    report = store.validate_integrity()
    assert report["ok"] is False
    codes = {item["code"] for item in report["issues"]}
    assert "master_plan_signature_mismatch" in codes
    assert "round_subplan_stage_mismatch" in codes



def test_legacy_round_manual_attribution_requires_audited_reason_and_cannot_be_overwritten():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)

    resolved = store.attribute_legacy_round(
        1, master["id"], reviewer="operator", reason="依据旧轮次创建时的人工记录确认版本",
    )
    assert resolved["master_plan_version_id"] == master["id"]
    assert resolved["plan_signature"] == master["plan_signature"]
    assert resolved["plan_attribution_status"] == "resolved"
    audit = conn.execute(
        "SELECT override_field,reason,overridden_by FROM user_override WHERE round_id=1"
    ).fetchone()
    assert tuple(audit) == (
        "legacy_plan_attribution", "依据旧轮次创建时的人工记录确认版本", "operator"
    )
    with pytest.raises(PlanVersionError, match="禁止覆盖"):
        store.attribute_legacy_round(
            1, master["id"], reviewer="operator", reason="不允许再次改绑",
        )



def test_subplan_stage_rejects_zero_target_and_rolls_back_partial_version():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    store.create_subplan(
        "sub-xhs", master_plan_version_id=master["id"],
        platform="xhs", title="小红书子计划",
    )
    with pytest.raises(PlanVersionError, match="目标篇数"):
        store.create_subplan_version(
            "sub-xhs", {"title": "invalid"}, master_plan_version_id=master["id"],
            stages=[{"title": "阶段一", "target_count": 0}],
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM sub_plan_versions WHERE sub_plan_id='sub-xhs'"
    ).fetchone()[0] == 0



def test_subplan_stage_resolver_advances_only_after_user_confirmed_publish():
    from src.core.subplan_production import active_subplan_stage

    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    store.create_subplan(
        "sub-stage", master_plan_version_id=master["id"],
        platform="xhs", title="阶段推进测试",
    )
    version = store.create_subplan_version(
        "sub-stage", {"title": "阶段推进测试", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[
            {"title": "阶段一", "target_count": 1},
            {"title": "阶段二", "target_count": 2},
        ],
    )
    store.activate_subplan_version(version["id"], reviewer="reviewer")
    stage_rows = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order",
        (version["id"],),
    ).fetchall()
    first_id, second_id = [int(row[0]) for row in stage_rows]

    current = active_subplan_stage(conn, today="2026-11-02")
    assert current["id"] == first_id
    assert current["remaining_count"] == 1

    conn.execute("INSERT INTO contents(id,round_id) VALUES('stage-content',1)")
    conn.execute(
        "UPDATE spiral_rounds SET master_plan_version_id=?,sub_plan_id=?,sub_plan_version_id=?,"
        "sub_plan_stage_id=?,sub_plan_snapshot='{}',sub_plan_stage_snapshot='{}' WHERE id=1",
        (master["id"], "sub-stage", version["id"], first_id),
    )
    conn.execute(
        "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('unpublished','stage-content',NULL)"
    )
    conn.commit()
    # Generated/saved draft does not advance the phase.
    assert active_subplan_stage(conn, today="2026-11-02")["id"] == first_id

    conn.execute("UPDATE drafts SET user_confirmed_at='2026-11-02' WHERE id='unpublished'")
    conn.commit()
    # A read-only lookup reports the completed threshold but does not mutate state.
    assert active_subplan_stage(conn, today="2026-11-02")["blocker"] == "stage_target_reached"
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (first_id,)
    ).fetchone()[0] == "pending"

    next_stage = active_subplan_stage(conn, today="2026-11-02", advance=True)
    assert next_stage["id"] == second_id
    assert next_stage["status"] == "pending"
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (first_id,)
    ).fetchone()[0] == "completed"


def test_create_round_once_persists_trigger_metadata():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)
    store.reserve_round_open(
        "metadata-key", "metadata-hash",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    created = store.create_round_once(
        "metadata-key", trigger_source="auto", applied_feedback='{"ok":true}',
    )
    row = conn.execute(
        "SELECT trigger_source,applied_feedback FROM spiral_rounds WHERE id=?",
        (created["round_id"],),
    ).fetchone()
    assert tuple(row) == ("auto", '{"ok":true}')



def test_stage_identity_and_publish_progress_survive_subplan_revision():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    first, first_stage_id = _active_subplan(store, master)
    store.bind_round(
        1, master_plan_version_id=master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=first["id"],
        sub_plan_stage_id=first_stage_id,
    )
    conn.execute("INSERT INTO contents(id,round_id) VALUES('content-revision',1)")
    conn.execute(
        "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-revision','content-revision','2026-11-02')"
    )
    conn.execute("UPDATE sub_plan_stages SET status='in_progress' WHERE id=?", (first_stage_id,))
    conn.commit()
    prior_stage = dict(conn.execute("SELECT * FROM sub_plan_stages WHERE id=?", (first_stage_id,)).fetchone())
    second = store.create_subplan_version(
        "sub-xhs", {"title": "小红书子计划修订"}, master_plan_version_id=master["id"],
        stages=[{key: prior_stage[key] for key in (
            "stage_key", "stage_order", "title", "direction", "target_count",
            "start_date", "end_date", "completion_condition", "status", "notes",
        )}],
        based_on_version_id=first["id"],
    )
    second_stage_id = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (second["id"],)
    ).fetchone()[0]
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (second_stage_id,)
    ).fetchone()[0] == "in_progress"
    assert store.get_stage_publish_stats(second_stage_id)["confirmed_published_count"] == 1


def test_stage_key_cannot_be_reused_from_another_subplan():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    first, first_stage_id = _active_subplan(store, master)
    first_stage_key = conn.execute(
        "SELECT stage_key FROM sub_plan_stages WHERE id=?", (first_stage_id,)
    ).fetchone()[0]
    store.create_subplan(
        "sub-douyin", master_plan_version_id=master["id"],
        platform="douyin", title="抖音子计划",
    )
    second = store.create_subplan_version(
        "sub-douyin", {"title": "抖音子计划"}, master_plan_version_id=master["id"],
        stages=[{"title": "第一阶段", "target_count": 3}],
    )
    with pytest.raises(PlanVersionError, match="必须继承"):
        store.create_subplan_version(
            "sub-douyin", {"title": "抖音子计划"}, master_plan_version_id=master["id"],
            based_on_version_id=second["id"],
            stages=[{"stage_key": first_stage_key, "title": "伪造阶段", "target_count": 3}],
        )


def test_delete_latest_master_restores_predecessor_and_only_switch_paused_children():
    conn = _db()
    store = PlanVersionStore(conn)
    first = _active_master(store)
    active_child, _ = _active_subplan(store, first)
    store.create_subplan(
        "sub-manual", master_plan_version_id=first["id"],
        platform="douyin", title="人工暂停子计划",
    )
    manual_version = store.create_subplan_version(
        "sub-manual", {"title": "人工暂停子计划"},
        master_plan_version_id=first["id"], stages=[{"title": "阶段"}],
    )
    store.activate_subplan_version(manual_version["id"], reviewer="reviewer")
    conn.execute(
        "UPDATE sub_plans SET status='paused',pause_reason='user' WHERE id='sub-manual'"
    )
    conn.execute(
        """INSERT INTO sub_plans(
               id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
           ) VALUES('sub-manual-after-switch',?,'kuaishou','切换后人工暂停','active','now','now','test')""",
        (first["id"],),
    )
    conn.execute(
        """INSERT INTO sub_plans(
               id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
           ) VALUES('sub-completed',?,'kuaishou','已完成','completed','now','now','test')""",
        (first["id"],),
    )
    conn.execute(
        """INSERT INTO sub_plans(
               id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
           ) VALUES('sub-cancelled',?,'wechat_channels','已取消','cancelled','now','now','test')""",
        (first["id"],),
    )
    conn.commit()

    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    store.activate_master_version(second["id"], reviewer="reviewer-2")
    auto_paused = conn.execute(
        "SELECT status,pause_reason,paused_by_master_version_id FROM sub_plans WHERE id='sub-xhs'"
    ).fetchone()
    assert tuple(auto_paused) == ("paused", "master_plan_switch", second["id"])
    assert conn.execute(
        "SELECT pause_reason FROM sub_plans WHERE id='sub-manual'"
    ).fetchone()[0] == "user"
    # An explicit user pause after automatic suspension overrides restoration provenance.
    conn.execute(
        """UPDATE sub_plans SET status='paused',pause_reason='user',
               paused_by_master_version_id=NULL WHERE id='sub-manual-after-switch'"""
    )
    conn.commit()

    # Keep a production round and its content/draft attached to the deleted master.
    conn.execute(
        "UPDATE spiral_rounds SET master_plan_version_id=? WHERE id=1", (second["id"],)
    )
    conn.execute("INSERT INTO contents(id,round_id) VALUES('kept-content',1)")
    conn.execute(
        "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('kept-draft','kept-content','2026-11-03')"
    )
    conn.commit()

    result = store.delete_master_version(second["id"], actor="operator")
    assert result["deleted_version"]["deleted_at"] is not None
    assert result["deleted_version"]["status"] == "archived"
    assert result["restored_version"]["id"] == first["id"]
    assert result["restored_version"]["status"] == "effective"
    assert conn.execute(
        "SELECT active_version_id FROM plan_settings WHERE id=1"
    ).fetchone()[0] == first["id"]
    assert tuple(conn.execute(
        "SELECT status,pause_reason,paused_by_master_version_id FROM sub_plans WHERE id='sub-xhs'"
    ).fetchone()) == ("active", None, None)
    assert tuple(conn.execute(
        "SELECT status,pause_reason FROM sub_plans WHERE id='sub-manual'"
    ).fetchone()) == ("paused", "user")
    assert conn.execute("SELECT status FROM sub_plans WHERE id='sub-completed'").fetchone()[0] == "completed"
    assert conn.execute("SELECT status FROM sub_plans WHERE id='sub-cancelled'").fetchone()[0] == "cancelled"
    assert tuple(conn.execute(
        "SELECT status,pause_reason,paused_by_master_version_id FROM sub_plans WHERE id='sub-manual-after-switch'"
    ).fetchone()) == ("paused", "user", None)
    assert conn.execute("SELECT COUNT(*) FROM drafts WHERE id='kept-draft'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT master_plan_version_id FROM spiral_rounds WHERE id=1"
    ).fetchone()[0] == second["id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM master_plan_versions WHERE status='effective' AND deleted_at IS NULL"
    ).fetchone()[0] == 1


def test_latest_undeleted_master_is_required_for_activation():
    conn = _db()
    store = PlanVersionStore(conn)
    first = _active_master(store)
    older_draft = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    latest_draft = store.create_master_draft(_plan(7), based_on_version_id=older_draft["id"])
    with pytest.raises(PlanVersionError, match="最新"):
        store.activate_master_version(older_draft["id"], reviewer="reviewer")
    store.activate_master_version(latest_draft["id"], reviewer="reviewer")
    assert conn.execute(
        "SELECT id FROM master_plan_versions WHERE status='effective'"
    ).fetchone()[0] == latest_draft["id"]


def test_master_delete_rolls_back_every_change_when_settings_row_is_missing():
    conn = _db()
    store = PlanVersionStore(conn)
    first = _active_master(store)
    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    store.activate_master_version(second["id"], reviewer="reviewer")
    conn.execute("DELETE FROM plan_settings WHERE id=1")
    conn.commit()

    with pytest.raises(PlanVersionError, match="设置记录缺失"):
        store.delete_master_version(second["id"], actor="operator")
    current = conn.execute(
        "SELECT status,deleted_at FROM master_plan_versions WHERE id=?", (second["id"],)
    ).fetchone()
    assert tuple(current) == ("effective", None)
    assert conn.execute(
        "SELECT status FROM master_plan_versions WHERE id=?", (first["id"],)
    ).fetchone()[0] == "archived"


def test_deleted_master_cannot_be_reactivated_or_deleted_twice():
    conn = _db()
    store = PlanVersionStore(conn)
    first = _active_master(store)
    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    store.activate_master_version(second["id"], reviewer="reviewer")
    store.delete_master_version(second["id"])
    with pytest.raises(PlanVersionError, match="已经删除"):
        store.delete_master_version(second["id"])
    with pytest.raises(PlanVersionError):
        store.activate_master_version(second["id"], reviewer="reviewer")


def test_delete_skips_rejected_draft_when_finding_previous_effective_master():
    conn = _db()
    store = PlanVersionStore(conn)
    first = _active_master(store)
    rejected = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    conn.execute(
        "UPDATE master_plan_versions SET status='archived',reviewed_at='2026-11-03',reviewed_by='reviewer' WHERE id=?",
        (rejected["id"],),
    )
    conn.commit()
    latest = store.create_master_draft(_plan(7), based_on_version_id=rejected["id"])
    store.activate_master_version(latest["id"], reviewer="reviewer")
    result = store.delete_master_version(latest["id"])
    assert result["restored_version"]["id"] == first["id"]
    assert conn.execute(
        "SELECT status FROM master_plan_versions WHERE id=?", (rejected["id"],)
    ).fetchone()[0] == "archived"


def test_concurrent_master_deletes_restore_once_without_duplicate_effective_versions(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "master-delete-race.db"
    setup = _db(path)
    store = PlanVersionStore(setup)
    first = _active_master(store)
    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    store.activate_master_version(second["id"], reviewer="reviewer")
    setup.close()

    barrier = Barrier(2)
    def delete_once():
        conn = sqlite3.connect(str(path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            PlanVersionStore(conn).delete_master_version(second["id"], actor="parallel")
            return "deleted"
        except PlanVersionError:
            return "conflict"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _n: delete_once(), range(2)))

    assert sorted(outcomes) == ["conflict", "deleted"]
    verify = sqlite3.connect(str(path))
    try:
        assert verify.execute(
            "SELECT id FROM master_plan_versions WHERE status='effective' AND deleted_at IS NULL"
        ).fetchone()[0] == first["id"]
        assert verify.execute(
            "SELECT COUNT(*) FROM master_plan_versions WHERE status='effective'"
        ).fetchone()[0] == 1
        assert verify.execute(
            "SELECT deleted_at IS NOT NULL FROM master_plan_versions WHERE id=?", (second["id"],)
        ).fetchone()[0] == 1
    finally:
        verify.close()


def test_concurrent_master_delete_and_new_review_cannot_break_single_effective_invariant(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "master-review-race.db"
    setup = _db(path)
    store = PlanVersionStore(setup)
    first = _active_master(store)
    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])
    store.activate_master_version(second["id"], reviewer="reviewer")
    third = store.create_master_draft(_plan(7), based_on_version_id=second["id"])
    setup.close()

    barrier = Barrier(2)
    def run(action):
        conn = sqlite3.connect(str(path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            if action == "delete":
                PlanVersionStore(conn).delete_master_version(second["id"], actor="parallel")
                return "deleted"
            PlanVersionStore(conn).activate_master_version(third["id"], reviewer="parallel")
            return "activated"
        except PlanVersionError:
            return "conflict"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, ("delete", "activate")))

    assert "activated" in outcomes
    verify = sqlite3.connect(str(path))
    try:
        effective = verify.execute(
            "SELECT id FROM master_plan_versions WHERE status='effective' AND deleted_at IS NULL"
        ).fetchall()
        assert [row[0] for row in effective] == [third["id"]]
        assert verify.execute(
            "SELECT COUNT(*) FROM master_plan_versions WHERE status='effective'"
        ).fetchone()[0] == 1
    finally:
        verify.close()
