from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.db import Database, Migration


LEGACY_MEDIA_131_CHECKSUM = "db83577353daffd3ebc4ce8707b544bdf3ddd6cfcc20fdf505f8e49e0d1dc4db"


def test_repository_migration_versions_are_unique():
    db = Database.__new__(Database)
    versions = [migration.version for migration in db._discover_migrations()]
    assert len(versions) == len(set(versions))


def _checksum_db(db: Database, checksum: str) -> None:
    db.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db.db_path)) as conn:
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, description TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE schema_migration_checksums ("
            "version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, verification_status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_version(version, description) VALUES (131, 'legacy migration')"
        )
        conn.execute(
            "INSERT INTO schema_migration_checksums(version, checksum, verification_status) "
            "VALUES (131, ?, 'applied')",
            (checksum,),
        )


def test_exact_legacy_media_131_checksum_is_accepted_for_master_131(cfg, tmp_path):
    db = Database(cfg)
    _checksum_db(db, LEGACY_MEDIA_131_CHECKSUM)
    migration_path = tmp_path / "131_master_plan_lifecycle_restore.sql"
    migration_path.write_text("-- current master migration\n", encoding="utf-8")

    db._verify_applied_migration_checksums(
        [Migration(131, migration_path, "master_plan_lifecycle_restore")], {131}
    )


def test_unrecognized_legacy_131_checksum_still_fails_closed(cfg):
    db = Database(cfg)
    _checksum_db(db, "not-a-known-checksum")
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "131_master_plan_lifecycle_restore.sql"

    with pytest.raises(RuntimeError, match="SQL 文件发生变化"):
        db._verify_applied_migration_checksums(
            [Migration(131, migration_path, "master_plan_lifecycle_restore")], {131}
        )


@pytest.mark.parametrize("legacy_media_131", [True, False])
def test_forward_reconciliation_restores_both_legacy_131_schema_shapes(cfg, legacy_media_131):
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    media_path = migration_dir / "134_media_generation_budget_usage.sql"
    master_path = migration_dir / "135_master_plan_lifecycle_reconcile.sql"

    db = Database(cfg)
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, description TEXT NOT NULL)"
        )
        media_columns = (
            ", api_calls INTEGER NOT NULL DEFAULT 0, failed_api_calls INTEGER NOT NULL DEFAULT 0"
            if legacy_media_131 else ""
        )
        master_columns = (
            ", deleted_at TEXT, deleted_by TEXT"
            if not legacy_media_131 else ""
        )
        subplan_columns = (
            ", pause_reason TEXT, paused_by_master_version_id INTEGER"
            if not legacy_media_131 else ""
        )
        conn.execute(
            "CREATE TABLE media_generation_jobs (id INTEGER PRIMARY KEY" + media_columns + ")"
        )
        conn.execute(
            "CREATE TABLE master_plan_versions (id INTEGER PRIMARY KEY, version_no INTEGER, "
            "status TEXT, effective_at TEXT" + master_columns + ")"
        )
        conn.execute(
            "CREATE TABLE sub_plans (id INTEGER PRIMARY KEY, master_plan_version_id INTEGER, "
            "status TEXT" + subplan_columns + ")"
        )

        # Historical media-131 databases already have media columns; canonical
        # master-131 databases already have lifecycle columns. Both then run the
        # forward migrations against the exact same migration runner.
        db._run_migration(conn, Migration(134, media_path, "media_generation_budget_usage"))
        db._run_migration(conn, Migration(135, master_path, "master_plan_lifecycle_reconcile"))

        media_columns_after = {row[1] for row in conn.execute("PRAGMA table_info(media_generation_jobs)")}
        master_columns_after = {row[1] for row in conn.execute("PRAGMA table_info(master_plan_versions)")}
        subplan_columns_after = {row[1] for row in conn.execute("PRAGMA table_info(sub_plans)")}
        assert {"api_calls", "failed_api_calls"} <= media_columns_after
        assert {"deleted_at", "deleted_by"} <= master_columns_after
        assert {"pause_reason", "paused_by_master_version_id"} <= subplan_columns_after
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name IN "
            "('trg_master_plan_latest_only_insert', 'trg_master_plan_latest_only_update', "
            "'trg_master_plan_no_delete_effective')"
        ).fetchone()[0] == 3
        assert {
            row[0] for row in conn.execute("SELECT version FROM schema_version")
        } == {134, 135}
    finally:
        conn.close()
