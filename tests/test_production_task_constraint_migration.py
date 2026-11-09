from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_170_removes_single_item_and_global_active_line_guards():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE production_tasks (
            id TEXT PRIMARY KEY,
            task_ordinal INTEGER NOT NULL,
            sub_plan_id TEXT
        );
        CREATE TABLE content_production_lines (
            id TEXT PRIMARY KEY,
            production_task_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
        );
        CREATE TRIGGER trg_production_tasks_single_item_ordinal
        BEFORE INSERT ON production_tasks
        WHEN NEW.sub_plan_id IS NOT NULL AND NEW.task_ordinal != 1
        BEGIN
            SELECT RAISE(ABORT, 'one content task per production line');
        END;
        CREATE UNIQUE INDEX idx_content_production_line_single_active
        ON content_production_lines((1))
        WHERE status IN ('pending','producing','awaiting_publish','failed');
    """)
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "170_remove_single_item_production_guards.sql"
    )
    conn.executescript(migration.read_text(encoding="utf-8"))

    # Multi-item tasks are now accepted.
    conn.execute(
        "INSERT INTO production_tasks(id,task_ordinal,sub_plan_id) VALUES('task-1',1,'sub-a')"
    )
    conn.execute(
        "INSERT INTO production_tasks(id,task_ordinal,sub_plan_id) VALUES('task-2',2,'sub-a')"
    )

    # Multiple independent active lines can coexist in the same tenant database.
    conn.execute(
        "INSERT INTO content_production_lines(id,production_task_id,status) VALUES('line-1','task-1','producing')"
    )
    conn.execute(
        "INSERT INTO content_production_lines(id,production_task_id,status) VALUES('line-2','task-2','producing')"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM content_production_lines WHERE status='producing'"
    ).fetchone()[0] == 2

    # Migration is safe to re-run by itself.
    conn.executescript(migration.read_text(encoding="utf-8"))
    conn.close()
