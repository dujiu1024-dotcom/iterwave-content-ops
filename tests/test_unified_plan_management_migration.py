"""Migration 142 preserves legacy batches and snapshots available strategy lineage."""
import sqlite3
from pathlib import Path


def test_content_plan_rounds_backfill_and_task_line_gate():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE master_plan_versions(id INTEGER PRIMARY KEY);
        CREATE TABLE sub_plans(id TEXT PRIMARY KEY, master_plan_version_id INTEGER);
        CREATE TABLE sub_plan_versions(
            id INTEGER PRIMARY KEY, sub_plan_id TEXT, master_plan_version_id INTEGER
        );
        CREATE TABLE sub_plan_stages(
            id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER, stage_key TEXT
        );
        CREATE TABLE production_lines(id TEXT PRIMARY KEY, sub_plan_id TEXT);
        CREATE TABLE production_line_versions(
            id INTEGER PRIMARY KEY, production_line_id TEXT, sub_plan_version_id INTEGER
        );
        CREATE TABLE spiral_rounds(
            id INTEGER PRIMARY KEY, master_plan_version_id INTEGER, sub_plan_id TEXT,
            sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER,
            production_line_version_id INTEGER
        );
        CREATE TABLE content_plan_rounds(
            plan_id TEXT NOT NULL, round_id INTEGER NOT NULL,
            task_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(plan_id, round_id)
        );
        CREATE TABLE production_tasks(
            id TEXT PRIMARY KEY, round_id INTEGER, master_plan_version_id INTEGER,
            sub_plan_id TEXT, sub_plan_version_id INTEGER, strategy_step_id TEXT,
            production_line_version_id INTEGER
        );
        INSERT INTO production_tasks(
            id,round_id,master_plan_version_id,sub_plan_id,
            sub_plan_version_id,strategy_step_id,production_line_version_id
        ) VALUES('legacy-task',11,NULL,NULL,NULL,NULL,NULL);

        INSERT INTO master_plan_versions VALUES(1);
        INSERT INTO sub_plans VALUES('sub-a',1);
        INSERT INTO sub_plan_versions VALUES(2,'sub-a',1);
        INSERT INTO sub_plan_stages VALUES(3,2,'step-a');
        INSERT INTO production_lines VALUES('line-a','sub-a');
        INSERT INTO production_line_versions VALUES(4,'line-a',2);
        INSERT INTO spiral_rounds VALUES(10,1,'sub-a',2,3,4);
        INSERT INTO spiral_rounds VALUES(11,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO content_plan_rounds VALUES('legacy-plan',10,'queue-task-1','2026-11-03');
        INSERT INTO content_plan_rounds VALUES('legacy-plan',11,NULL,'2026-11-03');
    """)

    migration = Path(__file__).resolve().parents[1] / "migrations" / "142_unified_plan_management.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))

    attributed = conn.execute(
        """SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,
                  sub_plan_stage_id,strategy_step_id,production_line_version_id,
                  attribution_status
           FROM content_plan_rounds WHERE round_id=10"""
    ).fetchone()
    assert attributed == (1,"sub-a",2,3,"step-a",4,"attributed")

    legacy = conn.execute(
        "SELECT attribution_status FROM content_plan_rounds WHERE round_id=11"
    ).fetchone()
    assert legacy == ("manual_review",)

    legacy_task = conn.execute(
        "SELECT production_line_version_id FROM production_tasks WHERE id='legacy-task'"
    ).fetchone()
    assert legacy_task == (None,)

    try:
        conn.execute(
            """INSERT INTO production_tasks(
                id,round_id,master_plan_version_id,sub_plan_id,
                sub_plan_version_id,strategy_step_id,production_line_version_id
            ) VALUES('task-no-line',10,1,'sub-a',2,'step-a',NULL)"""
        )
        raise AssertionError("task without a production line version was accepted")
    except sqlite3.IntegrityError as exc:
        assert "production line version" in str(exc)

    conn.close()
