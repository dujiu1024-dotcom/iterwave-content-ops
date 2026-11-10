from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.ensure_production_triggers import REQUIRED_TRIGGERS, _trigger_statements, ensure_triggers


def test_trigger_recovery_extracts_only_required_migration_150_triggers():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "150_content_production_lines_and_context.sql"
    )
    statements = _trigger_statements(migration.read_text(encoding="utf-8"))

    assert set(statements) == set(REQUIRED_TRIGGERS)
    assert "AFTER INSERT ON production_tasks" in statements["trg_content_production_line_after_task_insert"]
    assert "BEFORE UPDATE OF status ON production_tasks" in statements["trg_production_tasks_require_context_before_claim"]


def test_trigger_recovery_restores_missing_triggers_without_touching_rows(tmp_path):
    db_path = tmp_path / "tenant.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE production_tasks (
            id TEXT PRIMARY KEY, round_id INTEGER, content_id TEXT,
            sub_plan_id TEXT, production_line_version_id INTEGER,
            created_at TEXT, status TEXT
        );
        CREATE TABLE content_production_lines (
            id TEXT PRIMARY KEY, production_task_id TEXT, round_id INTEGER,
            content_id TEXT, status TEXT, source_production_line_version_id INTEGER,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE content_production_line_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_production_line_id TEXT, event_type TEXT,
            from_status TEXT, to_status TEXT, actor TEXT, reason TEXT, created_at TEXT
        );
        CREATE TABLE production_context_snapshots (
            production_task_id TEXT, context_status TEXT
        );
        INSERT INTO production_tasks(id, round_id, status) VALUES('existing-task', 1, 'pending');
    """)
    conn.commit()
    conn.close()

    report = ensure_triggers(db_path)

    assert set(report["restored"]) == set(REQUIRED_TRIGGERS)
    assert report["missing"] == []
    check = sqlite3.connect(db_path)
    try:
        assert check.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 1
        actual = {
            row[0] for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert set(REQUIRED_TRIGGERS).issubset(actual)
    finally:
        check.close()
