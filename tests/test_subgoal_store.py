from __future__ import annotations

import sqlite3

import pytest

from src.core.subgoal_store import SubGoalError, SubGoalStore, assert_stage_goals_ready


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE sub_plans(id TEXT PRIMARY KEY, current_version_id INTEGER);
    CREATE TABLE sub_plan_versions(id INTEGER PRIMARY KEY, sub_plan_id TEXT, status TEXT);
    CREATE TABLE sub_plan_stages(id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER);
    CREATE TABLE sub_goals(
      id TEXT PRIMARY KEY, sub_plan_version_id INTEGER, stage_id INTEGER, parent_goal_id TEXT,
      goal_type TEXT, goal_order INTEGER, priority INTEGER, title TEXT, description TEXT,
      target_count INTEGER, acceptance_criteria TEXT, requires_production INTEGER,
      status TEXT, completed_at TEXT, created_at TEXT, updated_at TEXT,
      UNIQUE(sub_plan_version_id,goal_order)
    );
    CREATE TABLE sub_goal_dependencies(goal_id TEXT,depends_on_goal_id TEXT,created_at TEXT);
    CREATE TABLE sub_goal_evidence(id INTEGER PRIMARY KEY,goal_id TEXT,decision TEXT,reason TEXT,
      evidence_json TEXT,actual_count INTEGER,reviewed_by TEXT,created_at TEXT);
    CREATE TABLE sub_goal_production_plans(id INTEGER PRIMARY KEY,goal_id TEXT UNIQUE,
      content_direction TEXT,suggested_count INTEGER,resource_requirements_json TEXT,priority INTEGER,
      execution_conditions TEXT,acceptance_relation TEXT,enabled INTEGER,created_at TEXT,updated_at TEXT);
    """)
    conn.execute("INSERT INTO sub_plans VALUES('p',1)")
    conn.execute("INSERT INTO sub_plan_versions VALUES(1,'p','draft')")
    conn.execute("INSERT INTO sub_plan_stages VALUES(10,1)")
    conn.commit()
    return conn


def test_subgoal_dependency_blocks_production_until_predecessor_accepted():
    conn = _db()
    store = SubGoalStore(conn)
    a = store.create_goal("p",1,{"goal_type":"quantity","title":"发布测试内容","target_count":2,
        "acceptance_criteria":"确认发布达到 2 篇","stage_id":10})
    b = store.create_goal("p",1,{"goal_type":"business","title":"验证转化","acceptance_criteria":"提供转化证据",
        "stage_id":10,"goal_order":2,"dependencies":[a["id"]]})
    store.save_production_plan("p",b["id"],{"content_direction":"转化验证","suggested_count":1,"execution_conditions":"前置目标通过后执行","acceptance_relation":"以转化证据验收"})
    with pytest.raises(SubGoalError,match="当前状态为 blocked"):
        assert_stage_goals_ready(conn,1,10)
    conn.execute("UPDATE sub_plan_versions SET status='effective' WHERE id=1")
    conn.commit()
    with pytest.raises(SubGoalError,match="前置子目标尚未验收通过"):
        store.accept("p",b["id"],{"decision":"passed","reason":"验收","evidence":{"ok":True}})
    assert store.accept("p",a["id"],{"decision":"passed","actual_count":2,
        "evidence":{"published_draft_ids":["d1","d2"]}})["status"] == "completed"
    assert_stage_goals_ready(conn,1,10)


def test_quantity_goal_cannot_pass_below_target_and_acceptance_is_append_only():
    conn = _db()
    store = SubGoalStore(conn)
    goal = store.create_goal("p",1,{"goal_type":"quantity","title":"数量目标","target_count":3,
        "acceptance_criteria":"达到三篇"})
    conn.execute("UPDATE sub_plan_versions SET status='effective' WHERE id=1")
    conn.commit()
    with pytest.raises(SubGoalError,match="未达到"):
        store.accept("p",goal["id"],{"decision":"passed","actual_count":2,"evidence":{"count":2}})
    assert store.accept("p",goal["id"],{"decision":"passed","actual_count":3,
        "evidence":{"count":3}})["status"] == "completed"
    assert conn.execute("SELECT COUNT(*) FROM sub_goal_evidence").fetchone()[0] == 1


def test_cycle_and_cross_version_dependencies_are_rejected():
    conn = _db()
    store = SubGoalStore(conn)
    a = store.create_goal("p",1,{"goal_type":"business","title":"A","acceptance_criteria":"证据"})
    b = store.create_goal("p",1,{"goal_type":"business","title":"B","acceptance_criteria":"证据",
        "goal_order":2,"dependencies":[a["id"]]})
    with pytest.raises(SubGoalError,match="循环"):
        store.update_goal("p",a["id"],{"dependencies":[b["id"]]})


def test_failed_and_needs_review_acceptance_statuses_are_not_erased_by_refresh():
    conn = _db()
    store = SubGoalStore(conn)
    goal = store.create_goal("p",1,{"goal_type":"business","title":"业务目标","acceptance_criteria":"业务证据"})
    conn.execute("UPDATE sub_plan_versions SET status='effective' WHERE id=1")
    conn.commit()
    failed = store.accept("p",goal["id"],{"decision":"failed","reason":"数据不足","evidence":{"sample":0}})
    assert failed["status"] == "failed"
    assert store.list_goals("p",1)[0]["status"] == "failed"
    reviewed = store.accept("p",goal["id"],{"decision":"needs_review","reason":"需补材料","evidence":{"sample":1}})
    assert reviewed["status"] == "needs_review"
    assert store.list_goals("p",1)[0]["status"] == "needs_review"


def test_goal_order_can_be_changed_without_duplicate_positions_and_status_is_server_owned():
    conn = _db()
    store = SubGoalStore(conn)
    first = store.create_goal("p",1,{"goal_type":"business","title":"第一","acceptance_criteria":"证据","goal_order":1})
    second = store.create_goal("p",1,{"goal_type":"business","title":"第二","acceptance_criteria":"证据","goal_order":2})
    updated = store.update_goal("p",first["id"],{"goal_order":2,"status":"completed"})
    assert updated["goal_order"] == 2
    assert updated["status"] == "ready"
    assert store._goal(second["id"])["goal_order"] == 1


def test_parent_hierarchy_cycles_and_cross_version_dependencies_are_rejected():
    conn = _db()
    conn.execute("INSERT INTO sub_plan_versions VALUES(2,'p','draft')")
    conn.commit()
    store = SubGoalStore(conn)
    parent = store.create_goal("p",1,{"goal_type":"business","title":"父","acceptance_criteria":"证据","goal_order":1})
    child = store.create_goal("p",1,{"goal_type":"business","title":"子","acceptance_criteria":"证据",
        "goal_order":2,"parent_goal_id":parent["id"]})
    with pytest.raises(SubGoalError,match="父子层级存在循环"):
        store.update_goal("p",parent["id"],{"parent_goal_id":child["id"]})
    other = store.create_goal("p",2,{"goal_type":"business","title":"其他版本","acceptance_criteria":"证据"})
    with pytest.raises(SubGoalError,match="同一子计划版本"):
        store.create_goal("p",1,{"goal_type":"business","title":"跨版本依赖","acceptance_criteria":"证据",
            "goal_order":3,"dependencies":[other["id"]]})
