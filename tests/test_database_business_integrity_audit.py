import sqlite3

from src.core.db import Database


def _audit(conn):
    conn.row_factory = sqlite3.Row
    database = object.__new__(Database)
    database.connect = lambda: conn
    database._discover_migrations = lambda: []
    return database.verify_schema()


def _business_integrity_schema(conn):
    conn.executescript(
        """
        CREATE TABLE business_profiles (id INTEGER, version_no INTEGER);
        CREATE TABLE business_profile_versions (version_no INTEGER, snapshot_json TEXT);
        CREATE TABLE business_catalog_items (details_json TEXT, field_evidence_json TEXT);
        CREATE TABLE master_plan_versions (id INTEGER, status TEXT);
        CREATE TABLE master_plan_business_snapshots (
            master_plan_version_id INTEGER, business_profile_version_no INTEGER,
            snapshot_json TEXT
        );
        CREATE TABLE original_assets (id INTEGER, current_version_no INTEGER, metadata_json TEXT);
        CREATE TABLE original_asset_versions (
            id INTEGER, original_asset_id INTEGER, version_no INTEGER, metadata_json TEXT
        );
        CREATE TABLE original_asset_analyses (result_json TEXT, warnings_json TEXT);
        CREATE TABLE production_task_asset_usages (original_asset_id INTEGER, asset_version_no INTEGER);
        CREATE TABLE plan_change_audit (
            affected_tasks_json TEXT, before_snapshot_json TEXT, after_snapshot_json TEXT
        );
        """
    )


def test_business_integrity_audit_reports_historical_data_damage():
    conn = sqlite3.connect(":memory:")
    _business_integrity_schema(conn)
    conn.executescript(
        """
        INSERT INTO business_profiles VALUES (1, 8);
        INSERT INTO business_profile_versions VALUES (7, '{"profile":{},"items":[]}');
        INSERT INTO business_profile_versions VALUES (9, '{broken');
        INSERT INTO business_catalog_items VALUES ('[]', '{broken');
        INSERT INTO master_plan_versions VALUES (1, 'effective');
        INSERT INTO master_plan_business_snapshots VALUES (2, 6, '{broken');
        INSERT INTO original_assets VALUES (3, 2, '{broken');
        INSERT INTO original_asset_versions VALUES (4, 3, 1, '{broken');
        INSERT INTO original_asset_analyses VALUES ('{broken', '[]');
        INSERT INTO production_task_asset_usages VALUES (3, 2);
        INSERT INTO plan_change_audit VALUES ('{broken', NULL, '{}');
        """
    )
    try:
        report = _audit(conn)
    finally:
        conn.close()

    violations = report["governance_data_violations"]
    assert violations["business_profile_current_version_missing"] == 1
    assert violations["business_profile_history_json_invalid"] == 1
    assert violations["business_catalog_json_invalid"] == 1
    assert violations["effective_master_plan_snapshot_missing"] == 1
    assert violations["master_plan_snapshot_json_invalid"] == 1
    assert violations["master_plan_snapshot_profile_version_missing"] == 1
    assert violations["original_asset_current_version_missing"] == 1
    assert violations["original_asset_metadata_json_invalid"] == 1
    assert violations["original_asset_version_metadata_json_invalid"] == 1
    assert violations["original_asset_analysis_json_invalid"] == 1
    assert violations["production_task_asset_usage_version_mismatch"] == 1
    assert violations["plan_change_audit_json_invalid"] == 1
    assert report["governance_data_integrity_check"] is False


def test_business_integrity_audit_accepts_consistent_historical_data():
    conn = sqlite3.connect(":memory:")
    _business_integrity_schema(conn)
    conn.executescript(
        """
        INSERT INTO business_profiles VALUES (1, 1);
        INSERT INTO business_profile_versions VALUES (1, '{"profile":{},"items":[]}');
        INSERT INTO business_catalog_items VALUES ('{}', '{}');
        INSERT INTO master_plan_versions VALUES (1, 'effective');
        INSERT INTO master_plan_business_snapshots VALUES (1, 1, '{"profile":{},"items":[]}');
        INSERT INTO original_assets VALUES (3, 1, '{}');
        INSERT INTO original_asset_versions VALUES (4, 3, 1, '{}');
        INSERT INTO original_asset_analyses VALUES ('{}', '[]');
        INSERT INTO production_task_asset_usages VALUES (3, 1);
        INSERT INTO plan_change_audit VALUES ('{"tasks":[]}', NULL, '{}');
        """
    )
    try:
        report = _audit(conn)
    finally:
        conn.close()

    assert report["governance_data_integrity_check"] is True
    assert all(value == 0 for value in report["governance_data_violations"].values())
