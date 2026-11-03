import sqlite3
from pathlib import Path

import pytest


def _connection_with_base_tables():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sub_plans (id TEXT PRIMARY KEY, platform TEXT NOT NULL);
        CREATE TABLE master_plan_versions (id INTEGER PRIMARY KEY);
        CREATE TABLE sub_plan_versions (
            id INTEGER PRIMARY KEY, sub_plan_id TEXT NOT NULL
        );
        CREATE TABLE sub_plan_stages (
            id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL
        );
        CREATE TABLE production_lines (
            id TEXT PRIMARY KEY, sub_plan_id TEXT NOT NULL,
            current_version_id INTEGER
        );
        CREATE TABLE production_line_versions (
            id INTEGER PRIMARY KEY, production_line_id TEXT NOT NULL,
            sub_plan_version_id INTEGER NOT NULL, based_on_version_id INTEGER
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, master_plan_version_id INTEGER NOT NULL,
            sub_plan_id TEXT NOT NULL, sub_plan_version_id INTEGER NOT NULL,
            sub_plan_stage_id INTEGER NOT NULL, production_line_version_id INTEGER
        );
        CREATE TABLE production_tasks (
            id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
            round_id INTEGER NOT NULL, master_plan_version_id INTEGER NOT NULL,
            sub_plan_id TEXT NOT NULL, sub_plan_version_id INTEGER NOT NULL,
            strategy_step_id TEXT NOT NULL, platform TEXT NOT NULL,
            production_line_version_id INTEGER, status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
            content_id TEXT
        );
        CREATE TABLE contents (
            id TEXT PRIMARY KEY, round_id INTEGER NOT NULL,
            platform TEXT NOT NULL, production_task_id TEXT
        );
        """
    )
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "140_database_relationship_integrity.sql"
    )
    conn.executescript(migration.read_text(encoding="utf-8"))
    conn.executescript(
        """
        INSERT INTO sub_plans(id,platform) VALUES ('sp-1','x'),('sp-2','x');
        INSERT INTO master_plan_versions(id) VALUES (1);
        INSERT INTO sub_plan_versions(id,sub_plan_id) VALUES (10,'sp-1'),(20,'sp-2');
        INSERT INTO sub_plan_stages(id,sub_plan_version_id,stage_key)
            VALUES (100,10,'stage-a'),(200,20,'stage-b');
        INSERT INTO spiral_rounds(
            id,master_plan_version_id,sub_plan_id,sub_plan_version_id,
            sub_plan_stage_id,production_line_version_id
        ) VALUES (1,1,'sp-1',10,100,NULL);
        INSERT INTO production_lines(id,sub_plan_id) VALUES ('line-1','sp-1'),('line-2','sp-2');
        INSERT INTO production_line_versions(
            id,production_line_id,sub_plan_version_id
        ) VALUES (1000,'line-1',10),(2000,'line-2',20);
        """
    )
    return conn


def test_task_provenance_is_enforced_by_database_trigger():
    conn = _connection_with_base_tables()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="provenance"):
            conn.execute(
                """
                INSERT INTO production_tasks(
                    id,idempotency_key,round_id,master_plan_version_id,
                    sub_plan_id,sub_plan_version_id,strategy_step_id,platform,
                    status,attempt_count,max_attempts
                ) VALUES ('bad','bad-key',1,1,'sp-1',10,'wrong-stage','x','pending',0,3)
                """
            )
        conn.execute(
            """
            INSERT INTO production_tasks(
                id,idempotency_key,round_id,master_plan_version_id,
                sub_plan_id,sub_plan_version_id,strategy_step_id,platform,
                status,attempt_count,max_attempts
            ) VALUES ('task-1','key-1',1,1,'sp-1',10,'stage-a','x','pending',0,3)
            """
        )
    finally:
        conn.close()


def test_content_and_task_links_are_synchronized_and_immutable():
    conn = _connection_with_base_tables()
    try:
        conn.execute(
            """
            INSERT INTO production_tasks(
                id,idempotency_key,round_id,master_plan_version_id,
                sub_plan_id,sub_plan_version_id,strategy_step_id,platform,
                status,attempt_count,max_attempts
            ) VALUES ('task-1','key-1',1,1,'sp-1',10,'stage-a','x','pending',0,3)
            """
        )
        conn.execute(
            "INSERT INTO contents(id,round_id,platform) VALUES ('content-1',1,'x')"
        )
        conn.execute(
            "UPDATE contents SET production_task_id='task-1' WHERE id='content-1'"
        )
        assert conn.execute(
            "SELECT content_id FROM production_tasks WHERE id='task-1'"
        ).fetchone()[0] == "content-1"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE production_tasks SET content_id='content-2' WHERE id='task-1'"
            )

        conn.execute(
            """
            INSERT INTO production_tasks(
                id,idempotency_key,round_id,master_plan_version_id,
                sub_plan_id,sub_plan_version_id,strategy_step_id,platform,
                status,attempt_count,max_attempts
            ) VALUES ('task-2','key-2',1,1,'sp-1',10,'stage-a','x','pending',0,3)
            """
        )
        conn.execute(
            "INSERT INTO contents(id,round_id,platform) VALUES ('content-2',1,'x')"
        )
        conn.execute(
            "UPDATE production_tasks SET content_id='content-2' WHERE id='task-2'"
        )
        assert conn.execute(
            "SELECT production_task_id FROM contents WHERE id='content-2'"
        ).fetchone()[0] == "task-2"
    finally:
        conn.close()


def test_current_production_line_version_must_belong_to_same_line():
    conn = _connection_with_base_tables()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="another line"):
            conn.execute(
                "UPDATE production_lines SET current_version_id=2000 WHERE id='line-1'"
            )
        conn.execute(
            "UPDATE production_lines SET current_version_id=1000 WHERE id='line-1'"
        )
    finally:
        conn.close()
