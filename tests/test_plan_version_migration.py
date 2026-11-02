import sqlite3
from pathlib import Path

from src.core.db import Database, Migration


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
        VALUES('sig-a','{"topic_count_per_platform":5,"dimension_allocation":{},"enabled_platforms":["xhs"],"content_format":"image_text"}',1,'2026-01-01','legacy');
        INSERT INTO plan_settings(id,plan_snapshot,plan_signature,updated_at,updated_by)
        VALUES(1,'{"topic_count_per_platform":5,"dimension_allocation":{},"enabled_platforms":["xhs"],"content_format":"image_text"}','sig-a','2026-01-01','legacy');
        INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at)
        VALUES(1,1,'completed','sig-a','2026-01-01');
        INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at)
        VALUES(2,2,'failed',NULL,'2026-01-02');
        INSERT INTO content_plans(id,title,topic,platforms_json,planned_count,scheduled_date,status,notes,version,created_at,updated_at)
        VALUES('cp-1','legacy plan','topic','["xhs"]',3,'2026-01-01','generated','',2,'2026-01-01','2026-01-02');
    """)
    return conn


def test_migration_restores_history_and_marks_missing_round_signature_pending():
    conn = _legacy_db()
    db = Database.__new__(Database)
    db._run_migration(conn, Migration(115, MIGRATION, "plan_version_lifecycle"))

    restored = conn.execute(
        "SELECT plan_signature,status,legacy_history_id FROM master_plan_versions"
    ).fetchall()
    assert len(restored) == 1
    assert restored[0]["plan_signature"] == "sig-a"
    assert restored[0]["status"] == "effective"
    assert restored[0]["legacy_history_id"] == 1

    round_one = conn.execute(
        "SELECT master_plan_version_id,plan_attribution_status FROM spiral_rounds WHERE id=1"
    ).fetchone()
    assert round_one["master_plan_version_id"] is not None
    assert round_one["plan_attribution_status"] == "resolved"
    round_two = conn.execute(
        "SELECT master_plan_version_id,plan_attribution_status FROM spiral_rounds WHERE id=2"
    ).fetchone()
    assert round_two["master_plan_version_id"] is None
    assert round_two["plan_attribution_status"] == "pending"

    subplan_version = conn.execute(
        "SELECT plan_id,version_no,status FROM content_plan_versions"
    ).fetchone()
    assert tuple(subplan_version) == ("cp-1", 2, "effective")
    conn.close()


def test_migration_is_safe_to_reapply_with_column_skipper():
    conn = _legacy_db()
    db = Database.__new__(Database)
    first = Database._skip_existing_columns(conn, MIGRATION.read_text(encoding="utf-8"))
    conn.executescript("BEGIN IMMEDIATE;\n" + first + "\nCOMMIT;")
    second = Database._skip_existing_columns(conn, MIGRATION.read_text(encoding="utf-8"))
    conn.executescript("BEGIN IMMEDIATE;\n" + second + "\nCOMMIT;")
    assert conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM content_plan_versions").fetchone()[0] == 1
    conn.close()

def test_stage_publish_stats_count_only_user_confirmed_drafts():
    from src.core.plan_version_store import PlanVersionStore

    conn = _legacy_db()
    db = Database.__new__(Database)
    db._run_migration(conn, Migration(115, MIGRATION, "plan_version_lifecycle"))
    conn.execute(
        "INSERT INTO spiral_rounds(id,round_number,status,plan_signature,created_at) "
        "VALUES(3,3,'created',NULL,'2026-01-03')"
    )
    conn.commit()

    store = PlanVersionStore(conn)
    master = store.create_master_draft({
        "topic_count_per_platform": 5, "dimension_allocation": {},
        "enabled_platforms": ["xhs"], "content_format": "image_text",
    })
    store.activate_master_version(master["id"], reviewer="reviewer")
    subplan = store.create_subplan_version(
        "cp-1", {"title": "subplan", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[{"title": "stage 1", "target_count": 2}],
    )
    stage_id = conn.execute(
        "SELECT id FROM content_plan_stages WHERE content_plan_version_id=?",
        (subplan["id"],),
    ).fetchone()[0]
    store.activate_subplan_version(subplan["id"])
    store.bind_round(
        3, master_plan_version_id=master["id"],
        content_plan_version_id=subplan["id"], content_plan_stage_id=stage_id,
    )
    conn.execute("INSERT INTO contents(id,round_id) VALUES('content-1',3)")
    conn.execute(
        "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-1','content-1',NULL)"
    )
    conn.execute(
        "INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-2','content-1','2026-01-04')"
    )
    conn.commit()

    assert store.get_stage_publish_stats(stage_id)["confirmed_published_count"] == 1
    store.update_stage_status(stage_id, "in_progress")
    store.update_stage_status(stage_id, "completed")
    assert conn.execute(
        "SELECT status FROM content_plan_stages WHERE id=?", (stage_id,)
    ).fetchone()[0] == "completed"
    conn.close()
