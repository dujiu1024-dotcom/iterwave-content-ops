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
    assert "strategy_step_condition_reviews" in report["missing_tables"]
    assert "idx_strategy_step_condition_reviews_stage" in report["missing_indexes"]
    assert "trg_production_tasks_validate_insert" in report["missing_triggers"]
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



def test_schema_audit_detects_orphaned_cross_table_relationships():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE production_tasks (
            id TEXT, round_id INTEGER, master_plan_version_id INTEGER,
            sub_plan_id TEXT, sub_plan_version_id INTEGER, strategy_step_id TEXT,
            platform TEXT, production_line_version_id INTEGER, content_id TEXT
        );
        CREATE TABLE production_lines (
            id TEXT, sub_plan_id TEXT, current_version_id INTEGER
        );
        CREATE TABLE production_line_versions (
            id INTEGER, production_line_id TEXT, sub_plan_version_id INTEGER,
            based_on_version_id INTEGER
        );
        CREATE TABLE spiral_rounds (
            id INTEGER, master_plan_version_id INTEGER, sub_plan_id TEXT,
            sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER,
            production_line_version_id INTEGER
        );
        CREATE TABLE sub_plans (id TEXT, platform TEXT);
        CREATE TABLE sub_plan_versions (id INTEGER, sub_plan_id TEXT);
        CREATE TABLE sub_plan_stages (id INTEGER, sub_plan_version_id INTEGER, stage_key TEXT);
        CREATE TABLE contents (id TEXT, round_id INTEGER, platform TEXT, production_task_id TEXT);

        INSERT INTO production_tasks VALUES ('task-orphan', 999, 1, 'sp-1', 10, 'stage-a', 'x', 404, NULL);
        INSERT INTO production_lines VALUES ('line-orphan', 'sp-1', 505);
        INSERT INTO production_line_versions VALUES (606, 'missing-line', 10, 707);
        """
    )
    try:
        report = _database_for_connection(conn).verify_schema()
    finally:
        conn.close()

    violations = report["relationship_violations"]
    assert violations["production_task_round_provenance"] == 1
    assert violations["production_task_line_versions"] == 1
    assert violations["production_line_current_versions"] == 1
    assert violations["production_line_version_subplans"] == 1
    assert violations["production_line_version_base_identity"] == 1


def test_schema_audit_detects_missing_migration_169_asset_usage_columns_and_index():
    # verify_schema() owns and closes the connection it opens. Keep a separate
    # anchor connection alive so fresh audit connections share this in-memory DB.
    uri = "file:migration_169_schema_audit?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    anchor.execute(
        """
        CREATE TABLE production_task_asset_usages (
            id INTEGER PRIMARY KEY,
            provider TEXT,
            model TEXT,
            source_sha256 TEXT,
            result_sha256 TEXT,
            generation_event_id TEXT,
            consumed_at TEXT,
            page_role TEXT
        )
        """
    )

    def connect_shared():
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    database = object.__new__(Database)
    database.connect = connect_shared
    database._discover_migrations = lambda: []
    try:
        report = database.verify_schema()
        assert report["missing_columns"] == [
            ("production_task_asset_usages", "page_index")
        ]
        assert "idx_production_task_asset_usage_generation_event" in report[
            "missing_indexes"
        ]
        assert (
            "production_task_asset_usages", ("generation_event_id",)
        ) in report["missing_unique_constraints"]

        anchor.execute(
            "ALTER TABLE production_task_asset_usages ADD COLUMN page_index INTEGER"
        )
        anchor.execute(
            """
            CREATE UNIQUE INDEX idx_production_task_asset_usage_generation_event
            ON production_task_asset_usages(generation_event_id)
            WHERE generation_event_id IS NOT NULL
            """
        )
        report = database.verify_schema()
        assert not any(
            table == "production_task_asset_usages"
            for table, _column in report["missing_columns"]
        )
        assert "idx_production_task_asset_usage_generation_event" not in report[
            "missing_indexes"
        ]
        assert (
            "production_task_asset_usages", ("generation_event_id",)
        ) not in report["missing_unique_constraints"]

        anchor.execute("DROP INDEX idx_production_task_asset_usage_generation_event")
        report = database.verify_schema()
        assert "idx_production_task_asset_usage_generation_event" in report[
            "missing_indexes"
        ]
        assert (
            "production_task_asset_usages", ("generation_event_id",)
        ) in report["missing_unique_constraints"]
    finally:
        anchor.close()
