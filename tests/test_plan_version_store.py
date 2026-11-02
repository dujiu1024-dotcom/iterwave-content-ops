import json
import sqlite3

import pytest

from src.core.plan_version_store import PlanVersionError, PlanVersionStore


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE plan_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), plan_snapshot TEXT NOT NULL,
            plan_signature TEXT NOT NULL, updated_at TEXT, updated_by TEXT,
            active_version_id INTEGER
        );
        CREATE TABLE signature_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, plan_signature TEXT NOT NULL,
            plan_snapshot TEXT NOT NULL, round_id INTEGER, created_at TEXT, author TEXT
        );
        CREATE TABLE master_plan_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, version_no INTEGER NOT NULL UNIQUE,
            plan_signature TEXT NOT NULL, plan_snapshot TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','effective','archived')),
            source TEXT NOT NULL, legacy_history_id INTEGER UNIQUE,
            based_on_version_id INTEGER, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT,
            effective_at TEXT, superseded_at TEXT, change_note TEXT
        );
        CREATE TABLE content_plans (
            id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
            master_plan_version_id INTEGER, updated_at TEXT
        );
        CREATE TABLE content_plan_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
            version_no INTEGER NOT NULL, master_plan_version_id INTEGER,
            snapshot_json TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            effective_at TEXT, change_note TEXT, UNIQUE(plan_id,version_no)
        );
        CREATE TABLE content_plan_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_plan_version_id INTEGER NOT NULL, stage_order INTEGER NOT NULL,
            title TEXT NOT NULL, target_count INTEGER NOT NULL,
            start_date TEXT, end_date TEXT, status TEXT NOT NULL,
            notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, round_number INTEGER, status TEXT,
            master_plan_version_id INTEGER, content_plan_version_id INTEGER,
            content_plan_stage_id INTEGER, plan_signature TEXT,
            plan_attribution_status TEXT
        );
    """)
    conn.execute("INSERT INTO content_plans(id,version) VALUES('cp-1',1)")
    conn.execute("INSERT INTO spiral_rounds(id,round_number,status) VALUES(1,1,'created')")
    conn.commit()
    return conn


def _plan(count=5):
    return {
        "topic_count_per_platform": count,
        "dimension_allocation": {"AI教育": count},
        "enabled_platforms": ["xhs"],
        "content_format": "image_text",
    }


def test_master_plan_draft_requires_explicit_activation_and_preserves_previous_version():
    conn = _db()
    store = PlanVersionStore(conn)
    first = store.create_master_draft(_plan(5))
    store.activate_master_version(first["id"], reviewer="reviewer-1")
    second = store.create_master_draft(_plan(6), based_on_version_id=first["id"])

    active = conn.execute(
        "SELECT id,plan_snapshot FROM master_plan_versions WHERE status='effective'"
    ).fetchone()
    assert active["id"] == first["id"]
    assert json.loads(active["plan_snapshot"])["topic_count_per_platform"] == 5
    assert conn.execute("SELECT active_version_id FROM plan_settings WHERE id=1").fetchone()[0] == first["id"]

    store.activate_master_version(second["id"], reviewer="reviewer-2")
    states = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM master_plan_versions")}
    assert states == {first["id"]: "archived", second["id"]: "effective"}
    assert conn.execute("SELECT active_version_id FROM plan_settings WHERE id=1").fetchone()[0] == second["id"]


def test_restore_historical_version_creates_new_draft_instead_of_reactivating_old_row():
    conn = _db()
    store = PlanVersionStore(conn)
    original = store.create_master_draft(_plan(5))
    store.activate_master_version(original["id"], reviewer="reviewer")
    restored = store.create_master_draft_from_version(original["id"])

    assert restored["status"] == "draft"
    assert restored["based_on_version_id"] == original["id"]
    assert restored["id"] != original["id"]
    assert conn.execute(
        "SELECT status FROM master_plan_versions WHERE id=?", (original["id"],)
    ).fetchone()[0] == "effective"


def test_subplan_stage_and_round_binding_are_version_specific():
    conn = _db()
    store = PlanVersionStore(conn)
    master = store.create_master_draft(_plan())
    store.activate_master_version(master["id"], reviewer="reviewer")

    subplan = store.create_subplan_version(
        "cp-1",
        {"title": "小红书子计划", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[{"title": "第一阶段", "target_count": 8}],
    )
    stage = conn.execute(
        "SELECT id FROM content_plan_stages WHERE content_plan_version_id=?",
        (subplan["id"],),
    ).fetchone()
    store.activate_subplan_version(subplan["id"])
    store.bind_round(
        1, master_plan_version_id=master["id"],
        content_plan_version_id=subplan["id"], content_plan_stage_id=stage["id"],
    )

    round_row = conn.execute(
        "SELECT master_plan_version_id,content_plan_version_id,content_plan_stage_id,plan_attribution_status "
        "FROM spiral_rounds WHERE id=1"
    ).fetchone()
    assert tuple(round_row) == (master["id"], subplan["id"], stage["id"], "resolved")


def test_round_cannot_bind_stage_from_another_subplan_version():
    conn = _db()
    store = PlanVersionStore(conn)
    master = store.create_master_draft(_plan())
    store.activate_master_version(master["id"], reviewer="reviewer")
    one = store.create_subplan_version(
        "cp-1", {"title": "v1"}, master_plan_version_id=master["id"],
        stages=[{"title": "阶段 A"}],
    )
    two = store.create_subplan_version(
        "cp-1", {"title": "v2"}, master_plan_version_id=master["id"],
        stages=[{"title": "阶段 B"}],
    )
    stage_one = conn.execute(
        "SELECT id FROM content_plan_stages WHERE content_plan_version_id=?", (one["id"],)
    ).fetchone()[0]
    with pytest.raises(PlanVersionError, match="不属于"):
        store.bind_round(
            1, master_plan_version_id=master["id"],
            content_plan_version_id=two["id"], content_plan_stage_id=stage_one,
        )
