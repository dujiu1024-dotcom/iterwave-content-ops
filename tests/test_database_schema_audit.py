import sqlite3

from src.core.db import Database


def _database_for_connection(conn):
    database = object.__new__(Database)
    database.connect = lambda: conn
    database._discover_migrations = lambda: []
    return database


def test_schema_audit_reports_missing_recent_business_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        report = _database_for_connection(conn).verify_schema()
    finally:
        conn.close()

    assert "production_tasks" in report["missing_tables"]
    assert "production_task_attempts" in report["missing_tables"]
    assert "content_review_events" in report["missing_tables"]
    assert "sub_plan_stage_outcomes" in report["missing_tables"]
    assert report["integrity_check"] == "ok"
    assert report["integrity_check_mode"] == "quick_check"


def test_schema_audit_reports_missing_columns_and_wrong_types():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE drafts (publish_status INTEGER, review_status TEXT)"
    )
    try:
        report = _database_for_connection(conn).verify_schema()
    finally:
        conn.close()

    assert ("drafts", "published_at") in report["missing_columns"]
    assert ("drafts", "publish_status", "TEXT", "INTEGER") in report[
        "column_type_mismatches"
    ]


def test_schema_audit_can_run_full_integrity_check():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        report = _database_for_connection(conn).verify_schema(full_integrity=True)
    finally:
        conn.close()

    assert report["integrity_check"] == "ok"
    assert report["integrity_check_mode"] == "integrity_check"
