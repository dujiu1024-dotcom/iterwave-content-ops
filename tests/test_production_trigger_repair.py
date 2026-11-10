from pathlib import Path

from scripts.verify_production_triggers import REQUIRED_TRIGGERS, _trigger_statements


def test_migration_150_contains_all_required_recoverable_triggers():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "150_content_production_lines_and_context.sql"
    )

    statements = _trigger_statements(migration.read_text(encoding="utf-8"))

    assert set(statements) == REQUIRED_TRIGGERS
    assert all("CREATE TRIGGER IF NOT EXISTS" in statement for statement in statements.values())
