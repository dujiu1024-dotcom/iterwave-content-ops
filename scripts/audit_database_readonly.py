#!/usr/bin/env python3
"""Strictly read-only SQLite audit using an isolated temporary snapshot.

The production database is opened with SQLite URI mode=ro. The SQLite backup
API copies a consistent snapshot to a temporary file; the application's
existing schema auditor runs only against that disposable copy.
No row values, client IPs, tokens, or content are printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from src.core.config import load_config
from src.core.db import Database


def readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def audit_auth_store(path: Path) -> dict:
    if not path.is_file():
        return {"file_present": False}
    with sqlite3.connect(readonly_uri(path), uri=True) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "auth_failures" not in tables:
            return {"file_present": True, "auth_failures_table_present": False}
        columns = {
            row[1] for row in conn.execute('PRAGMA table_info("auth_failures")')
        }
        if not {"ip", "failed_at"}.issubset(columns):
            return {
                "file_present": True,
                "auth_failures_table_present": True,
                "expected_columns_present": False,
            }
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ip) FROM auth_failures"
        ).fetchone()
        return {
            "file_present": True,
            "auth_failures_table_present": True,
            "expected_columns_present": True,
            "record_count": int(row[0]),
            "distinct_client_count": int(row[1]),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Production SQLite database path")
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--env", type=Path, default=None)
    parser.add_argument(
        "--historical-auth-db", type=Path,
        help="Optional extracted historical config/.auth_rate_limits.sqlite3 file",
    )
    args = parser.parse_args()

    source = args.db.expanduser().resolve()
    if not source.is_file():
        parser.error(f"database file not found: {source}")

    cfg = load_config(config_path=args.config, env_path=args.env)
    db = Database(cfg)

    with tempfile.TemporaryDirectory(prefix="iterwave-db-audit-") as temp_dir:
        snapshot = Path(temp_dir) / "audit-snapshot.sqlite3"
        # mode=ro prevents source writes; backup() reads a consistent SQLite snapshot.
        with sqlite3.connect(readonly_uri(source), uri=True) as src:
            with sqlite3.connect(snapshot) as dst:
                src.backup(dst)
        db.db_path = snapshot
        report = db.verify_schema(full_integrity=True)

        migrations = db._discover_migrations()
        checksum_report = {"checked": 0, "mismatches": []}
        with sqlite3.connect(snapshot) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if {"schema_version", "schema_migration_checksums"}.issubset(tables):
                applied = {
                    int(row[0]) for row in conn.execute("SELECT version FROM schema_version")
                }
                stored = {
                    int(row["version"]): row["checksum"]
                    for row in conn.execute(
                        "SELECT version, checksum FROM schema_migration_checksums"
                    )
                }
                for migration in migrations:
                    if migration.version not in applied:
                        continue
                    checksum_report["checked"] += 1
                    expected = hashlib.sha256(migration.path.read_bytes()).hexdigest()
                    if stored.get(migration.version) != expected:
                        checksum_report["mismatches"].append(migration.version)
                checksum_report["applied_versions"] = sorted(applied)
                checksum_report["missing_checksum_versions"] = sorted(applied - set(stored))

        # Keep only aggregate diagnostics; omit raw FK rows and any data samples.
        list_fields = (
            "missing_tables", "missing_views", "missing_indexes",
            "missing_foreign_keys", "missing_unique_constraints",
            "missing_columns", "column_type_mismatches", "nullable_mismatches",
            "default_mismatches", "missing_check_constraints",
            "missing_governance_indexes", "missing_governance_triggers",
            "missing_governance_columns", "missing_governance_foreign_keys",
        )
        summary = {
            "integrity_check": report.get("integrity_check"),
            "integrity_check_mode": report.get("integrity_check_mode"),
            "schema": {key: report.get(key, []) for key in list_fields},
            "unapplied_migrations": report.get("unapplied_migrations", []),
            "unknown_applied_migrations": report.get("unknown_applied_migrations", []),
            "foreign_key_violation_count": len(report.get("foreign_key_violations", [])),
            "relationship_violations": report.get("relationship_violations", {}),
            "governance_data_integrity_check": report.get("governance_data_integrity_check"),
            "governance_data_violations": report.get("governance_data_violations", {}),
            "migration_checksums": checksum_report,
        }
        if args.historical_auth_db:
            summary["historical_auth_store"] = audit_auth_store(
                args.historical_auth_db.expanduser().resolve()
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
