from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from src.core.asset_consumption_audit import (
    AssetConsumptionAuditError,
    record_visual_asset_consumption,
)


def _database(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE production_tasks (
            id TEXT PRIMARY KEY,
            input_snapshot_json TEXT NOT NULL
        );
        CREATE TABLE original_assets (id INTEGER PRIMARY KEY);
        CREATE TABLE original_asset_versions (
            original_asset_id INTEGER NOT NULL,
            version_no INTEGER NOT NULL,
            PRIMARY KEY(original_asset_id, version_no)
        );
        CREATE TABLE production_task_asset_usages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            production_task_id TEXT NOT NULL REFERENCES production_tasks(id),
            original_asset_id INTEGER NOT NULL REFERENCES original_assets(id),
            asset_version_no INTEGER NOT NULL,
            usage_role TEXT NOT NULL DEFAULT 'reference',
            permission_snapshot TEXT NOT NULL CHECK(permission_snapshot IN ('allowed','restricted')),
            privacy_snapshot TEXT NOT NULL DEFAULT 'unknown',
            asset_quality_snapshot TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(original_asset_id, asset_version_no)
                REFERENCES original_asset_versions(original_asset_id, version_no),
            UNIQUE(production_task_id, original_asset_id, asset_version_no, usage_role)
        );
        CREATE TRIGGER usage_immutable_update BEFORE UPDATE ON production_task_asset_usages
        BEGIN SELECT RAISE(ABORT, 'immutable'); END;
        CREATE TRIGGER usage_immutable_delete BEFORE DELETE ON production_task_asset_usages
        BEGIN SELECT RAISE(ABORT, 'immutable'); END;
    """)
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    conn.executescript((root / "migrations" / "169_production_task_asset_consumption_audit.sql").read_text())
    conn.execute("INSERT INTO production_tasks(id,input_snapshot_json) VALUES(?,?)", (
        "pt-1", json.dumps({"task_input": {
            "original_assets": [{
                "asset_id": 7, "version_no": 2, "sha256": "a" * 64,
                "review_status": "confirmed", "commercial_use_status": "allowed",
                "privacy_status": "clear", "readability_status": "readable",
                "completeness_status": "complete", "duplicate_status": "unique",
            }],
            "visual_asset_selection": {"selected_by_role": {
                "cover": [{"asset_id": 7, "version_no": 2, "sha256": "a" * 64}],
                "body": [], "ending": [],
            }},
        }}),
    ))
    conn.execute("INSERT INTO original_assets(id) VALUES(7)")
    conn.execute("INSERT INTO original_asset_versions(original_asset_id,version_no) VALUES(7,2)")
    conn.commit()
    conn.close()


def _record(path, **overrides):
    args = {
        "db_path": path,
        "production_task_id": "pt-1",
        "asset": {"asset_id": 7, "version_no": 2, "sha256": "a" * 64},
        "page_role": "cover",
        "page_index": 0,
        "provider": "test-provider",
        "model": "test-model",
        "result_sha256": hashlib.sha256(b"generated-image").hexdigest(),
        "prompt_fingerprint": "f" * 64,
    }
    args.update(overrides)
    return record_visual_asset_consumption(**args)


def test_consumption_is_appended_only_after_accepted_provider_result_and_is_idempotent(tmp_path):
    db = tmp_path / "audit.db"
    _database(db)
    first = _record(db)
    second = _record(db)
    assert first == second

    conn = sqlite3.connect(db)
    row = conn.execute("""
        SELECT usage_role,page_role,page_index,permission_snapshot,privacy_snapshot,provider,model,
               source_sha256,result_sha256,generation_event_id,consumed_at
        FROM production_task_asset_usages
    """).fetchone()
    assert row[0].startswith("consumed:cover:0:")
    assert row[1:3] == ("cover", 0)
    assert row[3:5] == ("allowed", "clear")
    assert row[5:7] == ("test-provider", "test-model")
    assert row[7] == "a" * 64
    assert row[8] == hashlib.sha256(b"generated-image").hexdigest()
    assert row[9] == first
    assert row[10]
    assert conn.execute("SELECT COUNT(*) FROM production_task_asset_usages").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE production_task_asset_usages SET model='changed'")
    conn.close()


def test_consumption_rejects_assets_not_selected_for_the_page_role(tmp_path):
    db = tmp_path / "audit.db"
    _database(db)
    with pytest.raises(AssetConsumptionAuditError, match="asset_not_selected_for_page_role"):
        _record(db, page_role="ending")


def test_consumption_rejects_permission_or_quality_regressions_in_task_snapshot(tmp_path):
    db = tmp_path / "audit.db"
    _database(db)
    conn = sqlite3.connect(db)
    snapshot = json.loads(conn.execute(
        "SELECT input_snapshot_json FROM production_tasks WHERE id='pt-1'"
    ).fetchone()[0])
    snapshot["task_input"]["original_assets"][0]["privacy_status"] = "restricted"
    conn.execute("UPDATE production_tasks SET input_snapshot_json=? WHERE id='pt-1'", (
        json.dumps(snapshot),
    ))
    conn.commit()
    conn.close()
    with pytest.raises(AssetConsumptionAuditError, match="asset_snapshot_permission_or_quality_denied"):
        _record(db)


def test_consumption_rejects_changed_asset_version_or_hash(tmp_path):
    db = tmp_path / "audit.db"
    _database(db)
    with pytest.raises(AssetConsumptionAuditError, match="asset_not_in_immutable_task_snapshot"):
        _record(db, asset={"asset_id": 7, "version_no": 3, "sha256": "a" * 64})


def test_different_provider_calls_are_distinct_consumption_events(tmp_path):
    db = tmp_path / "audit.db"
    _database(db)
    primary = _record(db, provider="primary-provider")
    fallback = _record(db, provider="fallback-provider")
    assert primary != fallback
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM production_task_asset_usages").fetchone()[0] == 2
    conn.close()
