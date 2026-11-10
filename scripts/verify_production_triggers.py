#!/usr/bin/env python3
"""Verify and restore the critical Migration 150 production-line triggers.

Run this after any database reset/restore operation:
    python scripts/verify_production_triggers.py --database data/easel.db

Only the four business-critical triggers listed below are restored. The script
never drops triggers, tables, indexes, or rows. It reads their canonical DDL
from Migration 150 so the repair cannot silently drift from the schema source.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

REQUIRED_TRIGGERS = {
    "trg_content_production_line_after_task_insert",
    "trg_production_task_status_to_content_line",
    "trg_content_production_line_transition_guard",
    "trg_production_tasks_require_context_before_claim",
}


def _trigger_statements(migration_sql: str) -> dict[str, str]:
    statements: dict[str, str] = {}
    buffer = ""
    for line in migration_sql.splitlines(keepends=True):
        buffer += line
        if not sqlite3.complete_statement(buffer):
            continue
        statement, buffer = buffer.strip(), ""
        match = re.search(
            r"CREATE\\s+TRIGGER\\s+(?:IF\\s+NOT\\s+EXISTS\\s+)?"
            r"([A-Za-z_][A-Za-z0-9_]*)",
            statement,
            flags=re.IGNORECASE,
        )
        if match and match.group(1) in REQUIRED_TRIGGERS:
            statements[match.group(1)] = statement
    missing_ddl = REQUIRED_TRIGGERS - statements.keys()
    if missing_ddl:
        raise RuntimeError(
            "Migration 150 缺少关键 trigger 定义：" + ", ".join(sorted(missing_ddl))
        )
    return statements


def verify_and_restore(database: Path, migration: Path) -> list[str]:
    if not database.is_file():
        raise FileNotFoundError(f"数据库文件不存在：{database}")
    if not migration.is_file():
        raise FileNotFoundError(f"Migration 150 文件不存在：{migration}")

    ddl = _trigger_statements(migration.read_text(encoding="utf-8"))
    restored: list[str] = []
    with sqlite3.connect(str(database), timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        missing = REQUIRED_TRIGGERS - existing
        if missing:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for name in sorted(missing):
                    conn.execute(ddl[name])
                    restored.append(name)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        final = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        still_missing = REQUIRED_TRIGGERS - final
        if still_missing:
            raise RuntimeError(
                "关键 trigger 修复后仍缺失：" + ", ".join(sorted(still_missing))
            )
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="账号组 SQLite 数据库路径")
    parser.add_argument(
        "--migration",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "migrations"
        / "150_content_production_lines_and_context.sql",
        help="Migration 150 SQL 文件路径",
    )
    args = parser.parse_args()
    restored = verify_and_restore(args.database, args.migration)
    if restored:
        print("已恢复关键 trigger：" + ", ".join(restored))
    else:
        print("关键生产 trigger 完整，无需修复")
    print("验证通过：4 个关键生产 trigger 均存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
