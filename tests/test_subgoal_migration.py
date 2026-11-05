from pathlib import Path
import sqlite3

import pytest


def test_subgoal_migration_creates_versioned_tables_and_append_only_evidence():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE sub_plan_versions(id INTEGER PRIMARY KEY);
        CREATE TABLE sub_plan_stages(id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER);
        CREATE TABLE production_tasks(id TEXT PRIMARY KEY);
        INSERT INTO sub_plan_versions VALUES(1);
        INSERT INTO sub_plan_stages VALUES(10,1);
    """)
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "149_subgoal_tree_dependency_gates.sql"
    )
    conn.executescript(migration.read_text(encoding="utf-8"))

    conn.execute(
        """INSERT INTO sub_goals(
            id,sub_plan_version_id,stage_id,goal_type,title,acceptance_criteria,created_at,updated_at
        ) VALUES('g1',1,10,'business','建立账号标签','提交账号标签证据','now','now')"""
    )
    conn.execute(
        """INSERT INTO sub_goal_evidence(
            goal_id,decision,reason,evidence_json,reviewed_by,created_at
        ) VALUES('g1','passed','','{}','tester','now')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE sub_goal_evidence SET reason='rewrite' WHERE goal_id='g1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM sub_goal_evidence WHERE goal_id='g1'")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(sub_goals)")}
    assert {"sub_plan_version_id","parent_goal_id","goal_type","acceptance_criteria","status","completed_at"} <= columns
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(sub_goals)")}
    assert "idx_sub_goals_version_tree" in indexes
    assert "idx_sub_goals_stage_status" in indexes
    conn.close()



def test_subgoal_actor_migration_marks_history_and_adds_author_columns():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE sub_plan_versions(id INTEGER PRIMARY KEY);
        CREATE TABLE sub_plan_stages(id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER);
        CREATE TABLE production_tasks(id TEXT PRIMARY KEY);
        CREATE TABLE content_production_line_events(id INTEGER PRIMARY KEY);
    """)
    root = Path(__file__).resolve().parents[1] / "migrations"
    conn.executescript((root / "149_subgoal_tree_dependency_gates.sql").read_text(encoding="utf-8"))
    conn.executescript("""
        INSERT INTO sub_goals(id,sub_plan_version_id,goal_type,title,acceptance_criteria,created_at,updated_at)
        VALUES('legacy-goal',1,'business','legacy','legacy criteria','now','now');
    """)
    conn.executescript((root / "157_subgoal_author_audit.sql").read_text(encoding="utf-8"))
    goal = conn.execute(
        "SELECT created_by,updated_by FROM sub_goals WHERE id='legacy-goal'"
    ).fetchone()
    assert goal == ("legacy:unattributed", "legacy:unattributed")
    plan_columns = {row[1] for row in conn.execute("PRAGMA table_info(sub_goal_production_plans)")}
    assert {"created_by", "updated_by"} <= plan_columns
    audit_columns = {row[1] for row in conn.execute("PRAGMA table_info(sub_goal_audit_events)")}
    assert {"tenant_id", "actor_type", "actor_id", "initiating_actor", "executing_actor", "task_id"} <= audit_columns
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(content_production_line_events)")}
    assert {"tenant_id", "actor_type", "actor_id", "initiating_actor", "executing_actor", "task_id"} <= event_columns
    conn.close()
