import sqlite3
from pathlib import Path

from src.core.db import Database, Migration
from src.core.plan_version_store import PlanVersionStore


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "115_plan_version_lifecycle.sql"


def _legacy_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE schema_version(version INTEGER PRIMARY KEY, description TEXT NOT NULL);
        CREATE TABLE signature_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, plan_signature TEXT NOT NULL,
            plan_snapshot TEXT NOT NULL, round_id INTEGER, created_at TEXT, author TEXT
        );
        CREATE TABLE plan_settings(
            id INTEGER PRIMARY KEY, plan_snapshot TEXT NOT NULL,
            plan_signature TEXT NOT NULL, updated_at TEXT, updated_by TEXT
        );
        CREATE TABLE spiral_rounds(
            id INTEGER PRIMARY KEY, round_number INTEGER, status TEXT,
            plan_signature TEXT, created_at TEXT
        );
        CREATE TABLE content_plans(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, topic TEXT NOT NULL,
            platforms_json TEXT NOT NULL, planned_count INTEGER NOT NULL,
            scheduled_date TEXT NOT NULL, status TEXT NOT NULL, notes TEXT NOT NULL,
            version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE contents(id TEXT PRIMARY KEY, round_id INTEGER);
        CREATE TABLE drafts(id TEXT PRIMARY KEY, content_id TEXT, user_confirmed_at TEXT);
        INSERT INTO signature_history(plan_signature,plan_snapshot,round_id,created_at,author)
        VALUES('sig-a','{"topic_count_per_platform":5,"dimension_allocation":{"AI教育":5},"enabled_platforms":["xhs"],"content_format":"image_text"}',1,'2026-01-01','legacy');
        INSERT INTO signature_history(plan_signature,plan_snapshot,round_id,created_at,author)
        VALUES('sig-a','{"topic_count_per_platform":5,"dimension_allocation":{"AI教育":5},"enabled_platforms":["xhs"],"content_format":"image_text"}',NULL,'2026-01-02','legacy-repeat');
        INSERT INTO plan_settings(id,plan_snapshot,plan_signature,updated_at,updated_by)
        VALUES(1,'{"topic_count_per_platform":5,"dimension_allocation":{"AI教育":5},"enabled_platforms":["xhs"],"content_format":"image_text"}','sig-a','2026-01-01','legacy');
        INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at)
        VALUES(1,1,'completed','sig-a','2026-01-01');
        INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at)
        VALUES(2,2,'failed',NULL,'2026-01-02');
        INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at)
        VALUES(4,4,'completed','sig-a','2026-01-04');
        INSERT INTO content_plans(id,title,topic,platforms_json,planned_count,scheduled_date,status,notes,version,created_at,updated_at)
        VALUES('cp-1','legacy calendar plan','topic','["xhs"]',3,'2026-01-01','generated','',2,'2026-01-01','2026-01-02');
    """)
    return conn


def test_migration_restores_history_and_marks_missing_round_signature_pending():
    conn = _legacy_db()
    db = Database.__new__(Database)
    db._run_migration(conn, Migration(115, MIGRATION, "plan_version_lifecycle"))
    stage_migration = Path(__file__).resolve().parents[1] / "migrations" / "116_subplan_round_metadata.sql"
    db._run_migration(conn, Migration(116, stage_migration, "subplan_round_metadata"))

    restored = conn.execute(
        "SELECT plan_signature,status,legacy_history_id FROM master_plan_versions"
    ).fetchall()
    assert len(restored) == 2
    assert {row["legacy_history_id"] for row in restored} == {1, 2}
    assert {row["status"] for row in restored} == {"archived", "effective"}
    assert all(row["plan_signature"] == "sig-a" for row in restored)

    round_one = conn.execute(
        "SELECT master_plan_version_id,plan_attribution_status FROM spiral_rounds WHERE id=1"
    ).fetchone()
    assert round_one["master_plan_version_id"] == 1
    assert round_one["plan_attribution_status"] == "resolved"
    assert conn.execute(
        "SELECT active_version_id FROM plan_settings WHERE id=1"
    ).fetchone()[0] == 2
    round_two = conn.execute(
        "SELECT master_plan_version_id,plan_attribution_status FROM spiral_rounds WHERE id=2"
    ).fetchone()
    assert round_two["master_plan_version_id"] is None
    assert round_two["plan_attribution_status"] == "pending"
    ambiguous_round = conn.execute(
        "SELECT master_plan_version_id,plan_attribution_status FROM spiral_rounds WHERE id=4"
    ).fetchone()
    assert ambiguous_round["master_plan_version_id"] is None
    assert ambiguous_round["plan_attribution_status"] == "manual_review"

    # The legacy content calendar schema is intentionally not repurposed or altered.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(content_plans)")}
    assert "platform" not in columns
    assert "master_plan_version_id" not in columns
    assert conn.execute("SELECT COUNT(*) FROM content_plans").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sub_plans").fetchone()[0] == 0
    conn.close()


def test_migration_is_safe_to_reapply_with_column_skipper():
    conn = _legacy_db()
    migration_sql = MIGRATION.read_text(encoding="utf-8")
    first = Database._skip_existing_columns(conn, migration_sql)
    conn.executescript("BEGIN IMMEDIATE;\n" + first + "\nCOMMIT;")
    second = Database._skip_existing_columns(conn, migration_sql)
    conn.executescript("BEGIN IMMEDIATE;\n" + second + "\nCOMMIT;")
    assert conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM sub_plans").fetchone()[0] == 0
    conn.close()


def test_stage_publish_stats_count_only_user_confirmed_drafts():
    conn = _legacy_db()
    db = Database.__new__(Database)
    db._run_migration(conn, Migration(115, MIGRATION, "plan_version_lifecycle"))
    stage_migration = Path(__file__).resolve().parents[1] / "migrations" / "116_subplan_round_metadata.sql"
    db._run_migration(conn, Migration(116, stage_migration, "subplan_round_metadata"))
    lifecycle_migration = Path(__file__).resolve().parents[1] / "migrations" / "131_master_plan_lifecycle_restore.sql"
    db._run_migration(conn, Migration(131, lifecycle_migration, "master_plan_lifecycle_restore"))
    strategy_migration = Path(__file__).resolve().parents[1] / "migrations" / "136_platform_subplan_strategy_model.sql"
    db._run_migration(conn, Migration(136, strategy_migration, "platform_subplan_strategy_model"))
    conn.execute(
        "INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at) "
        "VALUES(3,3,'created',NULL,'2026-01-03')"
    )
    conn.commit()

    store = PlanVersionStore(conn)
    master = store.create_master_draft({
        "topic_count_per_platform": 5, "dimension_allocation": {"AI教育": 5},
        "enabled_platforms": ["xhs"], "content_format": "image_text",
    })
    store.activate_master_version(master["id"], reviewer="reviewer")
    store.create_subplan(
        "sub-xhs", master_plan_version_id=master["id"],
        platform="xhs", title="小红书子计划",
    )
    subplan = store.create_subplan_version(
        "sub-xhs", {"title": "subplan", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[{"title": "stage 1", "target_count": 2}],
    )
    stage_id = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (subplan["id"],)
    ).fetchone()[0]
    store.activate_subplan_version(subplan["id"], reviewer="reviewer")
    store.bind_round(
        3, master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    conn.execute("INSERT INTO contents(id,round_id) VALUES('content-1',3)")
    conn.execute("INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-1','content-1',NULL)")
    conn.execute("INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-2','content-1','2026-01-04')")
    conn.commit()

    assert store.get_stage_publish_stats(stage_id)["confirmed_published_count"] == 1
    store.update_stage_status(stage_id, "in_progress")
    store.update_stage_status(stage_id, "completed")
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (stage_id,)
    ).fetchone()[0] == "completed"

    prior_stage = dict(conn.execute(
        "SELECT * FROM sub_plan_stages WHERE id=?", (stage_id,)
    ).fetchone())
    revised = store.create_subplan_version(
        "sub-xhs", {"title": "subplan revised"}, master_plan_version_id=master["id"],
        stages=[{key: prior_stage[key] for key in (
            "stage_key", "stage_order", "title", "direction", "target_count",
            "start_date", "end_date", "completion_condition", "status", "notes",
        )}],
        based_on_version_id=subplan["id"],
    )
    revised_stage_id = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (revised["id"],)
    ).fetchone()[0]
    assert store.get_stage_publish_stats(revised_stage_id)["confirmed_published_count"] == 1
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (revised_stage_id,)
    ).fetchone()[0] == "completed"
    conn.close()


def test_round_metadata_migration_adds_origin_and_feedback_columns():
    conn = _legacy_db()
    db = Database.__new__(Database)
    db._run_migration(conn, Migration(115, MIGRATION, "plan_version_lifecycle"))
    migration = Path(__file__).resolve().parents[1] / "migrations" / "116_subplan_round_metadata.sql"
    db._run_migration(conn, Migration(116, migration, "subplan_round_metadata"))
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(spiral_rounds)")}
    assert {"trigger_source", "applied_feedback"} <= columns
    stage_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sub_plan_stages)")}
    assert "stage_key" in stage_columns
    conn.execute(
        "INSERT INTO spiral_rounds(id,round_number,status) VALUES(9,9,'created')"
    )
    row = conn.execute(
        "SELECT trigger_source,applied_feedback FROM spiral_rounds WHERE id=9"
    ).fetchone()
    assert row["trigger_source"] == "manual"
    assert row["applied_feedback"] is None
    conn.close()


def test_master_plan_lifecycle_migration_adds_soft_delete_provenance_and_db_guards():
    conn = _legacy_db()
    db = Database.__new__(Database)
    for version, filename, name in (
        (115, "115_plan_version_lifecycle.sql", "plan_version_lifecycle"),
        (116, "116_subplan_round_metadata.sql", "subplan_round_metadata"),
        (130, "131_master_plan_lifecycle_restore.sql", "master_plan_lifecycle_restore"),
    ):
        path = Path(__file__).resolve().parents[1] / "migrations" / filename
        db._run_migration(conn, Migration(version, path, name))

    master_columns = {row["name"] for row in conn.execute("PRAGMA table_info(master_plan_versions)")}
    subplan_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sub_plans)")}
    assert {"deleted_at", "deleted_by"} <= master_columns
    assert {"pause_reason", "paused_by_master_version_id"} <= subplan_columns

    snapshot = '{"topic_count_per_platform":5,"dimension_allocation":{"AI教育":5},"enabled_platforms":["xhs"],"content_format":"image_text"}'
    conn.execute(
        """INSERT INTO master_plan_versions(
               version_no,plan_signature,plan_snapshot,status,source,created_at,created_by
           ) VALUES(1001,'sig-1',?,'draft','user','2026-11-03','test')""",
        (snapshot,),
    )
    first_id = conn.execute("SELECT id FROM master_plan_versions WHERE version_no=1001").fetchone()[0]
    conn.execute(
        """INSERT INTO master_plan_versions(
               version_no,plan_signature,plan_snapshot,status,source,created_at,created_by
           ) VALUES(1002,'sig-2',?,'draft','user','2026-11-03','test')""",
        (snapshot,),
    )
    second_id = conn.execute("SELECT id FROM master_plan_versions WHERE version_no=1002").fetchone()[0]
    conn.execute(
        """INSERT INTO master_plan_versions(
               version_no,plan_signature,plan_snapshot,status,source,created_at,created_by,
               reviewed_at,reviewed_by
           ) VALUES(1003,'sig-rejected',?,'archived','user','2026-11-03','test','2026-11-03','reviewer')""",
        (snapshot,),
    )

    import pytest
    conn.execute("UPDATE master_plan_versions SET status='archived' WHERE status='effective'")
    with pytest.raises(sqlite3.IntegrityError, match="latest undeleted"):
        conn.execute("UPDATE master_plan_versions SET status='effective' WHERE id=?", (first_id,))
    conn.execute(
        "UPDATE master_plan_versions SET status='effective',effective_at='2026-11-03' WHERE id=?",
        (second_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="archived before soft deletion"):
        conn.execute(
            "UPDATE master_plan_versions SET deleted_at='2026-11-03T00:00:00Z' WHERE id=?",
            (second_id,),
        )
    conn.execute(
        "UPDATE master_plan_versions SET status='archived',deleted_at='2026-11-03T00:00:00Z' WHERE id=?",
        (second_id,),
    )
    # A previously effective predecessor may be restored; a rejected archived draft
    # with no effective_at must not shadow it as the "latest" activatable plan.
    conn.execute("UPDATE master_plan_versions SET status='effective' WHERE id=?", (first_id,))
    conn.commit()
    assert conn.execute(
        "SELECT id FROM master_plan_versions WHERE status='effective' AND deleted_at IS NULL"
    ).fetchone()[0] == first_id
    conn.close()
