#!/usr/bin/env python3
"""Verify and restore the production-critical CPL triggers after a DB reset.

This script is intentionally non-destructive: it never drops triggers or changes
business rows. It replays only the four named CREATE TRIGGER statements from
Migration 150 when those triggers are absent.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "150_content_production_lines_and_context.sql"
REQUIRED_TRIGGERS = (
    "trg_content_production_line_after_task_insert",
    "trg_production_task_status_to_content_line",
    "trg_content_production_line_transition_guard",
    "trg_production_tasks_require_context_before_claim",
)


def _trigger_statements(sql: str) -> dict[str, str]:
    """Split SQLite statements safely, including multi-line BEGIN/END triggers."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise ValueError("Migration SQL ends with an incomplete statement")

    found: dict[str, str] = {}
    for statement in statements:
        match = re.search(
            r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
            statement,
            flags=re.IGNORECASE,
        )
        if match and match.group(1) in REQUIRED_TRIGGERS:
            found[match.group(1)] = statement
    missing_definitions = sorted(set(REQUIRED_TRIGGERS) - set(found))
    if missing_definitions:
        raise ValueError(
            "Migration 150 is missing required trigger definitions: "
            + ", ".join(missing_definitions)
        )
    return found


def ensure_triggers(db_path: Path, migration_path: Path = MIGRATION) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    definitions = _trigger_statements(migration_path.read_text(encoding="utf-8"))
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        missing_before = [name for name in REQUIRED_TRIGGERS if name not in existing]
        for name in missing_before:
            conn.execute(definitions[name])
        conn.commit()
        existing_after = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        missing_after = [name for name in REQUIRED_TRIGGERS if name not in existing_after]
        if missing_after:
            raise RuntimeError("Failed to restore required triggers: " + ", ".join(missing_after))
        return {
            "database": str(db_path),
            "checked": list(REQUIRED_TRIGGERS),
            "restored": missing_before,
            "missing": missing_after,
            "read_only": False,
            "business_rows_modified": False,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Tenant SQLite database path")
    parser.add_argument(
        "--migration", type=Path, default=MIGRATION,
        help="Migration 150 SQL path (defaults to the repository migration)",
    )
    args = parser.parse_args()
    report = ensure_triggers(args.db, args.migration)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
