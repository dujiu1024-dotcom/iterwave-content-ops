from __future__ import annotations

import sqlite3

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
