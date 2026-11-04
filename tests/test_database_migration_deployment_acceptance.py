"""DB-G3 migration deployment acceptance: fresh, incremental and tenant-isolated databases."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from src.core.db import Database, MIGRATIONS_DIR
from src.core.tenant_registry import TenantRegistry


def _database_for_path(cfg, db_file: Path, backup_dir: Path) -> Database:
    paths = cfg.paths.model_copy(update={
        "db_file": str(db_file),
        "data_dir": str(db_file.parent),
        "backup_dir": str(backup_dir),
    })
    return Database(cfg.model_copy(update={"paths": paths}))


def _assert_latest_database(db: Database) -> None:
    report = db.verify_schema(full_integrity=True)
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violations"] == []
    assert report["missing_tables"] == []
    assert report["missing_views"] == []
    assert report["missing_indexes"] == []
    assert report["missing_foreign_keys"] == []
    assert report["missing_unique_constraints"] == []
    assert report["unapplied_migrations"] == []
    assert report["unknown_applied_migrations"] == []
    assert report["governance_data_integrity_check"] is True

    migrations = db._discover_migrations()
    expected = {migration.version for migration in migrations}
    conn = db.connect()
    try:
        applied = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_version")
        }
        checksums = {
            int(row["version"]): row["checksum"]
            for row in conn.execute(
                "SELECT version, checksum FROM schema_migration_checksums"
            )
        }
        assert applied == expected
        assert set(checksums) == expected
        for migration in migrations:
            assert checksums[migration.version] == hashlib.sha256(
                migration.path.read_bytes()
            ).hexdigest()
    finally:
        conn.close()


def test_fresh_database_upgrade_is_complete_repeatable_and_checksum_verified(
    cfg, tmp_path,
):
    db = Database(cfg)
    db.initialize()
    _assert_latest_database(db)

    # A second startup must be a no-op and preserve both schema and data.
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE deployment_probe (value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO deployment_probe(value) VALUES ('preserve-on-restart')"
        )
        conn.commit()
    finally:
        conn.close()

    db.initialize()
    _assert_latest_database(db)
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT value FROM deployment_probe"
        ).fetchone()[0] == "preserve-on-restart"
    finally:
        conn.close()


def test_incremental_upgrade_from_previous_release_preserves_existing_rows(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    source_migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    previous_release = [
        path for path in source_migrations
        if path.stem.split("_", 1)[0].isdigit()
        and int(path.stem.split("_", 1)[0]) <= 145
    ]
    assert previous_release

    for path in previous_release:
        shutil.copy2(path, migration_dir / path.name)
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)

    db = Database(cfg)
    db.initialize()
    conn = db.connect()
    try:
        assert conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0] == 145
        conn.execute(
            "CREATE TABLE legacy_release_probe (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO legacy_release_probe(id, payload) VALUES ('legacy-1', 'keep-this-row')"
        )
        conn.commit()
    finally:
        conn.close()

    # Simulate deploying the next release with the complete migration set.
    for path in source_migrations:
        if path not in previous_release:
            shutil.copy2(path, migration_dir / path.name)

    db.initialize()
    db.initialize()
    _assert_latest_database(db)
    conn = db.connect()
    try:
        assert tuple(conn.execute(
            "SELECT id, payload FROM legacy_release_probe WHERE id='legacy-1'"
        ).fetchone()) == ("legacy-1", "keep-this-row")
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version IN (146, 147, 148)"
        ).fetchone()[0] == 3
    finally:
        conn.close()


def test_each_registered_tenant_database_migrates_independently_and_isolated(
    cfg, tmp_path,
):
    project_root = tmp_path / "workspace"
    registry = TenantRegistry(project_root)
    registry.initialize()
    registry.create("tenant_alpha", "租户 Alpha")
    registry.create("tenant_beta", "租户 Beta")

    tenant_databases = {}
    for tenant_id in ("default", "tenant_alpha", "tenant_beta"):
        paths = registry.paths(tenant_id, create=True)
        db = _database_for_path(cfg, paths.db_file, paths.backup_dir)
        db.initialize()
        _assert_latest_database(db)

        conn = db.connect()
        try:
            conn.execute(
                "CREATE TABLE tenant_deployment_probe (tenant_id TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO tenant_deployment_probe(tenant_id) VALUES (?)",
                (tenant_id,),
            )
            conn.commit()
        finally:
            conn.close()
        tenant_databases[tenant_id] = db

    for tenant_id, db in tenant_databases.items():
        conn = db.connect()
        try:
            values = [
                row[0] for row in conn.execute(
                    "SELECT tenant_id FROM tenant_deployment_probe"
                )
            ]
            assert values == [tenant_id]
        finally:
            conn.close()
