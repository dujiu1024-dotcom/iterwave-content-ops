from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "144_original_asset_inventory.sql"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE media_assets (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            original_filename TEXT NOT NULL DEFAULT '', stored_filename TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT 'image/jpeg', byte_size INTEGER NOT NULL DEFAULT 1,
            sha256 TEXT NOT NULL DEFAULT '', width INTEGER NOT NULL DEFAULT 1,
            height INTEGER NOT NULL DEFAULT 1, alt_text TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]', source_type TEXT NOT NULL DEFAULT 'upload',
            source_ref TEXT, status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE content_assets (
            id INTEGER PRIMARY KEY, asset_type TEXT NOT NULL DEFAULT 'reference',
            title TEXT NOT NULL DEFAULT '', body TEXT, source_url TEXT, platform TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE production_tasks (id TEXT PRIMARY KEY);
        """
    )
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    return conn


def test_original_asset_inventory_migration_creates_expected_tables():
    conn = _connection()
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "original_assets",
        "original_asset_versions",
        "original_asset_links",
        "original_asset_analyses",
        "original_asset_gaps",
        "production_task_asset_usages",
    } <= names


def test_original_asset_sources_are_references_and_unique():
    conn = _connection()
    conn.execute("INSERT INTO media_assets(id) VALUES (1)")
    conn.execute(
        """
        INSERT INTO original_assets(
            asset_type, title, source_kind, source_media_asset_id
        ) VALUES ('product_image', '产品图', 'media_asset', 1)
        """
    )
    try:
        conn.execute(
            """
            INSERT INTO original_assets(
                asset_type, title, source_kind, source_media_asset_id
            ) VALUES ('product_image', '重复登记', 'media_asset', 1)
            """
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("one media file must have one inventory record")


def test_asset_version_and_task_usage_snapshots_are_immutable():
    conn = _connection()
    conn.execute("INSERT INTO media_assets(id) VALUES (1)")
    conn.execute("INSERT INTO production_tasks(id) VALUES ('task-1')")
    conn.execute(
        """
        INSERT INTO original_assets(
            id, asset_type, title, source_kind, source_media_asset_id
        ) VALUES (1, 'product_image', '产品图', 'media_asset', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO original_asset_versions(
            original_asset_id, version_no, title, review_status,
            commercial_use_status
        ) VALUES (1, 1, '产品图', 'confirmed', 'allowed')
        """
    )
    conn.execute(
        """
        INSERT INTO production_task_asset_usages(
            production_task_id, original_asset_id, asset_version_no,
            permission_snapshot
        ) VALUES ('task-1', 1, 1, 'allowed')
        """
    )
    try:
        conn.execute(
            "UPDATE original_asset_versions SET title='篡改' WHERE original_asset_id=1 AND version_no=1"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("asset versions must be immutable")
    try:
        conn.execute(
            "UPDATE production_task_asset_usages SET permission_snapshot='restricted' WHERE production_task_id='task-1'"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("task asset usage snapshots must be immutable")
