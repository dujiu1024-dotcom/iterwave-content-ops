import json
import sqlite3

import pytest

from src.core.plan_version_store import PlanVersionError, PlanVersionStore


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
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
        CREATE TABLE sub_plans (
            id TEXT PRIMARY KEY, master_plan_version_id INTEGER NOT NULL,
            platform TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
            current_version_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL, archived_at TEXT
        );
        CREATE TABLE sub_plan_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sub_plan_id TEXT NOT NULL,
            version_no INTEGER NOT NULL, master_plan_version_id INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL, status TEXT NOT NULL, based_on_version_id INTEGER,
            created_at TEXT NOT NULL, created_by TEXT NOT NULL, reviewed_at TEXT,
            reviewed_by TEXT, review_note TEXT, effective_at TEXT, superseded_at TEXT,
            change_note TEXT, UNIQUE(sub_plan_id,version_no)
        );
        CREATE TABLE sub_plan_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sub_plan_version_id INTEGER NOT NULL,
            stage_order INTEGER NOT NULL, title TEXT NOT NULL, direction TEXT NOT NULL,
            target_count INTEGER NOT NULL, start_date TEXT, end_date TEXT,
            completion_condition TEXT NOT NULL, status TEXT NOT NULL, notes TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, goal_id TEXT, round_number INTEGER, status TEXT,
            topic_ids TEXT, content_variants TEXT,
            master_plan_version_id INTEGER, sub_plan_id TEXT, sub_plan_version_id INTEGER,
            sub_plan_stage_id INTEGER, sub_plan_snapshot TEXT, sub_plan_stage_snapshot TEXT,
            plan_signature TEXT, plan_attribution_status TEXT
        );
        CREATE TABLE round_open_idempotency (
            idempotency_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
            master_plan_version_id INTEGER NOT NULL, sub_plan_id TEXT NOT NULL,
            sub_plan_version_id INTEGER NOT NULL, sub_plan_stage_id INTEGER NOT NULL,
            status TEXT NOT NULL, round_id INTEGER UNIQUE, lease_until TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error_message TEXT
        );
        CREATE TABLE contents(id TEXT PRIMARY KEY, round_id INTEGER);
        CREATE TABLE drafts(id TEXT PRIMARY KEY, content_id TEXT, user_confirmed_at TEXT);
        CREATE VIEW sub_plan_stage_publish_stats AS
        SELECT s.id AS stage_id,s.sub_plan_version_id,
               COUNT(DISTINCT d.id) AS confirmed_published_count
        FROM sub_plan_stages s
        LEFT JOIN spiral_rounds r ON r.sub_plan_stage_id=s.id
            AND r.sub_plan_version_id=s.sub_plan_version_id
        LEFT JOIN contents c ON c.round_id=r.id
        LEFT JOIN drafts d ON d.content_id=c.id AND d.user_confirmed_at IS NOT NULL
        GROUP BY s.id,s.sub_plan_version_id;
        INSERT INTO spiral_rounds(id,round_number,status) VALUES(1,1,'created');
    """)
    return conn


def _plan(count=5):
    return {
        "topic_count_per_platform": count,
        "dimension_allocation": {"AI教育": count},
        "enabled_platforms": ["xhs"],
        "content_format": "image_text",
    }


def _active_master(store):
    row = store.create_master_draft(_plan())
    store.activate_master_version(row["id"], reviewer="reviewer")
    return row


def _active_subplan(store, master):
    store.create_subplan(
        "sub-xhs", master_plan_version_id=master["id"],
        platform="xhs", title="小红书子计划",
    )
    version = store.create_subplan_version(
        "sub-xhs", {"title": "小红书子计划", "platform": "xhs"},
        master_plan_version_id=master["id"],
        stages=[{"title": "第一阶段", "target_count": 8, "direction": "AI教育"}],
    )
    store.activate_subplan_version(version["id"], reviewer="reviewer")
    stage_id = store.conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()[0]
    return version, stage_id


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
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)
    store.bind_round(
        1, master_plan_version_id=master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=subplan["id"],
        sub_plan_stage_id=stage_id,
    )

    round_row = conn.execute(
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id,"
        "plan_attribution_status,sub_plan_snapshot,sub_plan_stage_snapshot "
        "FROM spiral_rounds WHERE id=1"
    ).fetchone()
    assert tuple(round_row[:5]) == (master["id"], "sub-xhs", subplan["id"], stage_id, "resolved")
    assert json.loads(round_row["sub_plan_snapshot"])["title"] == "小红书子计划"
    assert json.loads(round_row["sub_plan_stage_snapshot"])["title"] == "第一阶段"


def test_round_cannot_bind_stage_from_another_subplan_version():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    first, stage_one = _active_subplan(store, master)
    second = store.create_subplan_version(
        "sub-xhs", {"title": "v2"}, master_plan_version_id=master["id"],
        stages=[{"title": "阶段 B"}], based_on_version_id=first["id"],
    )
    store.activate_subplan_version(second["id"], reviewer="reviewer")
    with pytest.raises(PlanVersionError, match="不属于"):
        store.bind_round(
            1, master_plan_version_id=master["id"],
            sub_plan_id="sub-xhs", sub_plan_version_id=second["id"],
            sub_plan_stage_id=stage_one,
        )


def test_stage_publish_stats_count_only_user_confirmed_drafts_and_stage_lifecycle():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)
    store.bind_round(
        1, master_plan_version_id=master["id"],
        sub_plan_id="sub-xhs", sub_plan_version_id=subplan["id"],
        sub_plan_stage_id=stage_id,
    )
    conn.execute("INSERT INTO contents(id,round_id) VALUES('content-1',1)")
    conn.execute("INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-1','content-1',NULL)")
    conn.execute("INSERT INTO drafts(id,content_id,user_confirmed_at) VALUES('draft-2','content-1','2026-01-04')")
    conn.commit()

    assert store.get_stage_publish_stats(stage_id)["confirmed_published_count"] == 1
    store.update_stage_status(stage_id, "in_progress")
    store.update_stage_status(stage_id, "completed")
    assert conn.execute(
        "SELECT status FROM sub_plan_stages WHERE id=?", (stage_id,)
    ).fetchone()[0] == "completed"
    with pytest.raises(PlanVersionError, match="不允许"):
        store.update_stage_status(stage_id, "in_progress")


def test_durable_round_open_lock_is_idempotent_and_rejects_key_reuse():
    conn = _db()
    store = PlanVersionStore(conn)
    master = _active_master(store)
    subplan, stage_id = _active_subplan(store, master)

    first = store.reserve_round_open(
        "auto:2026-11-02T10:00", "hash-a",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    duplicate = store.reserve_round_open(
        "auto:2026-11-02T10:00", "hash-a",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    assert first["acquired"] is True
    assert duplicate["acquired"] is False
    assert duplicate["status"] == "in_progress"

    with pytest.raises(PlanVersionError, match="不同开轮请求"):
        store.reserve_round_open(
            "auto:2026-11-02T10:00", "hash-b",
            master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
            sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
        )

    completed = store.create_round_once(
        "auto:2026-11-02T10:00", goal_id="goal-1",
        topic_ids=["topic-1"], content_variants=[{"platform": "xhs"}],
    )
    assert completed["status"] == "created"
    assert completed["deduped"] is False
    assert tuple(conn.execute(
        "SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id "
        "FROM spiral_rounds WHERE id=?", (completed["round_id"],)
    ).fetchone()) == (master["id"], "sub-xhs", subplan["id"], stage_id)
    retry = store.reserve_round_open(
        "auto:2026-11-02T10:00", "hash-a",
        master_plan_version_id=master["id"], sub_plan_id="sub-xhs",
        sub_plan_version_id=subplan["id"], sub_plan_stage_id=stage_id,
    )
    assert retry == {"acquired": False, "status": "created", "round_id": completed["round_id"]}
