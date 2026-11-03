from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.db import Database


def _migration(path, text):
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_fresh_database_migrations_are_repeatable_and_duplicate_content_is_aliased(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE historical_rows (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
        INSERT INTO historical_rows(id, payload) VALUES ('row-1', 'preserve-me');
        """,
    )
    alter = """
        ALTER TABLE historical_rows ADD COLUMN legacy_flag INTEGER NOT NULL DEFAULT 0;
    """
    _migration(migration_dir / "002_add_legacy_flag.sql", alter)
    _migration(migration_dir / "003_duplicate_legacy_flag.sql", alter)

    db = Database(cfg)
    db.initialize()
    db.initialize()

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT payload, legacy_flag FROM historical_rows WHERE id='row-1'"
        ).fetchone()
        assert tuple(row) == ("preserve-me", 0)
        versions = {
            int(r[0]) for r in conn.execute("SELECT version FROM schema_version")
        }
        assert versions == {1, 2, 3}
        assert conn.execute(
            "SELECT COUNT(*) FROM historical_rows WHERE id='row-1'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_non_contiguous_historical_schema_executes_missing_lower_version_migration(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS historical_rows (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
        """,
    )
    _migration(
        migration_dir / "002_add_legacy_flag.sql",
        """
        ALTER TABLE historical_rows ADD COLUMN legacy_flag INTEGER NOT NULL DEFAULT 0;
        """,
    )
    _migration(
        migration_dir / "003_add_lookup_index.sql",
        """
        CREATE INDEX IF NOT EXISTS idx_historical_rows_payload ON historical_rows(payload);
        """,
    )

    db = Database(cfg)
    db.db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(str(db.db_path))
    legacy.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, description TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO schema_version(version, description) VALUES (3, 'historically applied later migration')"
    )
    legacy.execute(
        "CREATE TABLE historical_rows (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO historical_rows(id, payload) VALUES ('legacy-1', 'old-data')"
    )
    legacy.execute("CREATE INDEX idx_historical_rows_payload ON historical_rows(payload)")
    legacy.commit()
    legacy.close()

    db.initialize()
    conn = db.connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(historical_rows)")}
        assert "legacy_flag" in columns
        assert tuple(conn.execute(
            "SELECT payload, legacy_flag FROM historical_rows WHERE id='legacy-1'"
        ).fetchone()) == ("old-data", 0)
        versions = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_version")
        }
        assert versions == {1, 2, 3}
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_historical_rows_payload'"
        ).fetchone()
        assert index is not None
    finally:
        conn.close()


def test_legacy_partial_alter_is_completed_without_duplicate_column(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_legacy_base.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE historical_rows (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            legacy_flag INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO historical_rows(id, payload) VALUES ('legacy-2', 'kept');
        """,
    )
    _migration(
        migration_dir / "002_add_two_columns.sql",
        """
        ALTER TABLE historical_rows ADD COLUMN legacy_flag INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE historical_rows ADD COLUMN extra_note TEXT;
        """,
    )

    db = Database(cfg)
    db.initialize()
    conn = db.connect()
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(historical_rows)")]
        assert cols.count("legacy_flag") == 1
        assert "extra_note" in cols
        assert tuple(conn.execute(
            "SELECT payload, legacy_flag, extra_note FROM historical_rows WHERE id='legacy-2'"
        ).fetchone()) == ("kept", 0, None)
    finally:
        conn.close()



def test_missing_lower_duplicate_is_aliased_when_higher_version_was_applied(
    cfg, tmp_path, monkeypatch,
):
    """A legacy DB may record only the later of two identical migrations."""
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY
        );
        """,
    )
    duplicate_sql = "INSERT INTO audit_events(id) VALUES ('event-once');"
    _migration(migration_dir / "002_original.sql", duplicate_sql)
    _migration(migration_dir / "003_duplicate_alias.sql", duplicate_sql)

    db = Database(cfg)
    db.db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(str(db.db_path))
    legacy.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, description TEXT NOT NULL)"
    )
    legacy.executemany(
        "INSERT INTO schema_version(version, description) VALUES (?, ?)",
        [(1, "initial"), (3, "later duplicate migration already applied")],
    )
    legacy.execute("CREATE TABLE audit_events (id TEXT PRIMARY KEY)")
    legacy.execute("INSERT INTO audit_events(id) VALUES ('event-once')")
    legacy.commit()
    legacy.close()

    db.initialize()
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
        versions = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_version")
        }
        assert versions == {1, 2, 3}
    finally:
        conn.close()



def test_verify_schema_reports_tables_from_recent_migrations(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_empty_schema.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        """,
    )
    db = Database(cfg)
    db.initialize()

    missing = set(db.verify_schema()["missing_tables"])
    assert {
        "content_plans",
        "content_plan_rounds",
        "tenant_skill_registry",
        "tenant_skill_executions",
        "master_plan_versions",
        "sub_plan_stages",
        "draft_save_verifications",
        "activation_cohort",
        "weekly_action_feedback",
        "app_settings",
        "autogrow_runs",
        "orchestration_settings",
        "notify_rules",
        "platform_styles",
        "account_snapshots",
        "platform_accounts",
        "hot_topic_import_audit",
    } <= missing
    missing_indexes = set(db.verify_schema()["missing_indexes"])
    assert "idx_visual_jobs_queue" in missing_indexes
    assert "idx_ops_logs_round_id" in missing_indexes
    assert "idx_metric_snapshots_content_id_time" in missing_indexes
    assert "idx_skill_executions_skill_time" in missing_indexes
    assert "sub_plan_stage_publish_stats" in db.verify_schema()["missing_views"]
    assert ("ops_logs", "round_id", "spiral_rounds", "id", "NO ACTION") in db.verify_schema()["missing_foreign_keys"]



def test_concurrent_initializers_execute_non_idempotent_migration_once(
    cfg, tmp_path, monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE startup_events (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO startup_events(value) VALUES ('initialized');
        """,
    )
    db1, db2 = Database(cfg), Database(cfg)
    barrier = threading.Barrier(2)
    original1, original2 = db1._get_applied_versions, db2._get_applied_versions

    def applied1():
        result = original1()
        barrier.wait(timeout=5)
        return result

    def applied2():
        result = original2()
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(db1, "_get_applied_versions", applied1)
    monkeypatch.setattr(db2, "_get_applied_versions", applied2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(db.initialize) for db in (db1, db2)]
        for future in futures:
            future.result(timeout=15)

    conn = db1.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM startup_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=1").fetchone()[0] == 1
    finally:
        conn.close()



def test_database_backup_is_consistent_with_uncheckpointed_wal(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE durable_rows (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        """,
    )
    db = Database(cfg)
    db.initialize()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO durable_rows(value) VALUES ('committed-in-wal')")
        conn.commit()
        # Keep the live WAL connection open while the backup is produced.
        backup = db._backup()
        assert backup.is_file()
        assert not Path(str(backup) + "-wal").exists()
        snapshot = sqlite3.connect(str(backup))
        try:
            assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert snapshot.execute(
                "SELECT value FROM durable_rows"
            ).fetchone()[0] == "committed-in-wal"
        finally:
            snapshot.close()
    finally:
        conn.close()



def test_verify_schema_reports_foreign_key_violations(
    cfg, tmp_path, monkeypatch,
):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_fk_schema.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE parent_rows (id INTEGER PRIMARY KEY);
        CREATE TABLE child_rows (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER REFERENCES parent_rows(id)
        );
        """,
    )
    db = Database(cfg)
    db.initialize()

    # Simulate a legacy/imported writer that bypassed SQLite FK enforcement.
    raw = sqlite3.connect(str(db.db_path))
    try:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("INSERT INTO child_rows(parent_id) VALUES (999)")
        raw.commit()
    finally:
        raw.close()

    result = db.verify_schema()
    assert result["foreign_key_violations"]
    assert result["integrity_check"] == "ok"



def test_sql_statement_parser_preserves_trigger_bodies_and_inline_statements():
    from src.core.db import _iter_sql_statements

    sql = """
    CREATE TABLE source_rows (id INTEGER PRIMARY KEY);
    CREATE TABLE audit_rows (value INTEGER);
    CREATE TRIGGER source_insert_audit AFTER INSERT ON source_rows
    BEGIN
        INSERT INTO audit_rows(value) VALUES (NEW.id);
        INSERT INTO audit_rows(value) VALUES (NEW.id + 100);
    END;
    INSERT INTO source_rows(id) VALUES (1); INSERT INTO source_rows(id) VALUES (2);
    """
    statements = list(_iter_sql_statements(sql))
    conn = sqlite3.connect(":memory:")
    try:
        for statement in statements:
            conn.execute(statement)
        assert conn.execute("SELECT COUNT(*) FROM audit_rows").fetchone()[0] == 4
    finally:
        conn.close()



def test_migration_128_adds_missing_foreign_key_child_indexes(cfg, tmp_path, monkeypatch):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    source_migration = Path(__file__).resolve().parents[1] / "migrations" / "128_foreign_key_child_indexes.sql"
    _migration(
        migration_dir / "128_foreign_key_child_indexes.sql",
        source_migration.read_text(encoding="utf-8"),
    )
    _migration(
        migration_dir / "001_fk_index_schema.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE ops_logs (round_id INTEGER);
        CREATE TABLE contents (topic_id TEXT);
        CREATE TABLE user_override (round_id INTEGER, ai_call_id INTEGER);
        CREATE TABLE platform_insights (round_id INTEGER);
        CREATE TABLE signature_history (round_id INTEGER);
        CREATE TABLE autogrow_runs (round_id INTEGER);
        CREATE TABLE content_events (
            sample_run_id TEXT, sample_content_id TEXT, event_at TEXT
        );
        CREATE TABLE metric_snapshots (
            draft_id TEXT, content_id TEXT, measured_at TEXT, correction_of TEXT
        );
        CREATE TABLE media_generation_jobs (output_asset_id INTEGER);
        CREATE TABLE master_plan_versions (based_on_version_id INTEGER);
        CREATE TABLE sub_plan_versions (based_on_version_id INTEGER);
        CREATE TABLE round_open_idempotency (
            master_plan_version_id INTEGER,
            sub_plan_version_id INTEGER,
            sub_plan_stage_id INTEGER
        );
        """,
    )

    db = Database(cfg)
    db.initialize()
    conn = db.connect()
    try:
        expected = {
            "idx_ops_logs_round_id",
            "idx_user_override_round_id",
            "idx_user_override_ai_call_id",
            "idx_contents_topic_id",
            "idx_signature_history_round_id",
            "idx_autogrow_runs_round_id",
            "idx_content_events_sample_run",
            "idx_content_events_sample_content",
            "idx_metric_snapshots_draft_time",
            "idx_metric_snapshots_content_id_time",
            "idx_metric_snapshots_correction_of",
            "idx_media_generation_jobs_asset",
            "idx_master_plan_based_on_version",
            "idx_sub_plan_versions_based_on_version",
            "idx_round_open_master_version",
            "idx_round_open_sub_plan_version",
            "idx_round_open_sub_plan_stage",
        }
        actual = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert expected <= actual
    finally:
        conn.close()



def test_applied_migration_checksum_drift_fails_closed(cfg, tmp_path, monkeypatch):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        CREATE TABLE checksum_probe (value TEXT NOT NULL);
        """,
    )
    _migration(
        migration_dir / "002_add_probe_index.sql",
        "CREATE INDEX idx_checksum_probe_value ON checksum_probe(value);",
    )
    _migration(
        migration_dir / "129_migration_checksums.sql",
        """
        CREATE TABLE schema_migration_checksums (
            version INTEGER PRIMARY KEY REFERENCES schema_version(version) ON DELETE CASCADE,
            checksum TEXT NOT NULL,
            verification_status TEXT NOT NULL
                CHECK (verification_status IN ('baseline','applied','alias')),
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    )

    db = Database(cfg)
    db.initialize()

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT version, verification_status FROM schema_migration_checksums ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in rows] == [1, 2, 129]
        assert rows[0]["verification_status"] == "baseline"
        assert rows[1]["verification_status"] == "baseline"
        assert rows[2]["verification_status"] == "applied"
    finally:
        conn.close()

    _migration(
        migration_dir / "002_add_probe_index.sql",
        "CREATE INDEX idx_checksum_probe_value ON checksum_probe(value); -- changed",
    )
    with pytest.raises(RuntimeError, match="SQL 文件发生变化"):
        db.initialize()



def test_migration_discovery_rejects_duplicate_version_numbers(cfg, tmp_path, monkeypatch):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(migration_dir / "128_first.sql", "SELECT 1;")
    _migration(migration_dir / "128_second.sql", "SELECT 2;")

    with pytest.raises(RuntimeError, match="重复的数据库迁移版本号"):
        Database(cfg)._discover_migrations()



def test_current_migration_set_satisfies_schema_contract(cfg):
    db = Database(cfg)
    db.initialize()
    report = db.verify_schema()

    assert report["missing_tables"] == []
    assert report["missing_views"] == []
    assert report["missing_indexes"] == []
    assert report["missing_foreign_keys"] == []
    assert report["missing_unique_constraints"] == []
    assert report["unapplied_migrations"] == []
    assert report["unknown_applied_migrations"] == []
    assert report["foreign_key_violations"] == []
    assert report["integrity_check"] == "ok"



def test_verify_schema_reports_unknown_applied_migration_versions(cfg, tmp_path, monkeypatch):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    monkeypatch.setattr("src.core.db.MIGRATIONS_DIR", migration_dir)
    _migration(
        migration_dir / "001_initial.sql",
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL
        );
        """,
    )
    db = Database(cfg)
    db.initialize()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO schema_version(version, description) VALUES (999, 'unknown')"
        )
        conn.commit()
    finally:
        conn.close()

    report = db.verify_schema()
    assert report["unknown_applied_migrations"] == [999]
    assert report["unapplied_migrations"] == []
