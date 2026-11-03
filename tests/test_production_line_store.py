import sqlite3
from pathlib import Path

import pytest

from src.core.production_line_store import ProductionLineError, ProductionLineStore


MIGRATION = (Path(__file__).resolve().parents[1] / "migrations" / "139_production_line_lifecycle.sql").read_text(encoding="utf-8")


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE master_plan_versions(id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE sub_plans(
            id TEXT PRIMARY KEY, master_plan_version_id INTEGER NOT NULL,
            platform TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
            current_version_id INTEGER
        );
        CREATE TABLE sub_plan_versions(
            id INTEGER PRIMARY KEY, sub_plan_id TEXT NOT NULL,
            version_no INTEGER NOT NULL, master_plan_version_id INTEGER NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE sub_plan_stages(
            id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER NOT NULL,
            stage_key TEXT NOT NULL, stage_order INTEGER NOT NULL, title TEXT NOT NULL,
            target_count INTEGER NOT NULL, dependencies_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE spiral_rounds(id INTEGER PRIMARY KEY);
        CREATE TABLE production_tasks(id TEXT PRIMARY KEY, status TEXT);
    """)
    db.executescript(MIGRATION)
    db.execute("INSERT INTO master_plan_versions VALUES(1,'effective')")
    db.execute("INSERT INTO sub_plans VALUES('sub-1',1,'xhs','测试子计划','active',10)")
    db.execute("INSERT INTO sub_plan_versions VALUES(10,'sub-1',1,1,'effective')")
    db.execute("INSERT INTO sub_plan_stages VALUES(100,10,'stage-a',1,'建立标签',4,'[]','pending')")
    db.execute("INSERT INTO sub_plan_stages VALUES(101,10,'stage-b',2,'分析需求',6,'[\"stage-a\"]','pending')")
    db.commit()
    yield db
    db.close()


def definition():
    return {
        "title": "小红书生产线",
        "description": "按策略步骤生产并交付审核",
        "stages": [
            {"stage_key": "stage-a", "batch_size": 2, "production_goal": "建立标签"},
            {"stage_key": "stage-b", "batch_size": 3, "production_goal": "分析需求"},
        ],
    }


def test_create_and_activate_line_binds_exact_subplan_version(conn):
    store = ProductionLineStore(conn)
    draft = store.create_draft("sub-1", 10, definition(), generation_source="ai")
    assert draft["status"] == "draft"
    assert draft["generation_source"] == "ai"
    assert draft["definition"]["stages"][1]["stage_key"] == "stage-b"

    active = store.activate(draft["id"])
    assert active["status"] == "effective"
    line = store.get_for_subplan("sub-1")
    assert line["status"] == "active"
    assert line["current_version_id"] == draft["id"]


def test_line_creation_joins_outer_transaction_and_rolls_back_with_it(conn):
    store = ProductionLineStore(conn)
    conn.execute("BEGIN IMMEDIATE")
    draft = store.create_draft("sub-1", 10, definition())
    assert conn.in_transaction
    assert conn.execute(
        "SELECT COUNT(*) FROM production_lines WHERE sub_plan_id='sub-1'"
    ).fetchone()[0] == 1
    conn.rollback()
    assert conn.execute(
        "SELECT COUNT(*) FROM production_lines WHERE sub_plan_id='sub-1'"
    ).fetchone()[0] == 0


def test_line_must_cover_every_stage_in_strategy_order(conn):
    store = ProductionLineStore(conn)
    incomplete = definition()
    incomplete["stages"] = incomplete["stages"][:1]
    with pytest.raises(ProductionLineError, match="覆盖"):
        store.create_draft("sub-1", 10, incomplete)

    reordered = definition()
    reordered["stages"].reverse()
    with pytest.raises(ProductionLineError, match="既定顺序"):
        store.create_draft("sub-1", 10, reordered)


def test_batch_size_cannot_exceed_strategy_target(conn):
    store = ProductionLineStore(conn)
    invalid = definition()
    invalid["stages"][0]["batch_size"] = 5
    with pytest.raises(ProductionLineError, match="不能超过"):
        store.create_draft("sub-1", 10, invalid)
    fractional = definition()
    fractional["stages"][0]["batch_size"] = 1.5
    with pytest.raises(ProductionLineError, match="正整数"):
        store.create_draft("sub-1", 10, fractional)


def test_activation_rejects_stale_subplan_version(conn):
    store = ProductionLineStore(conn)
    draft = store.create_draft("sub-1", 10, definition())
    conn.execute("UPDATE sub_plans SET current_version_id=11 WHERE id='sub-1'")
    conn.commit()
    with pytest.raises(ProductionLineError, match="版本已过期"):
        store.activate(draft["id"])


def test_line_pause_and_resume_require_valid_parent_authorization(conn):
    store = ProductionLineStore(conn)
    draft = store.create_draft("sub-1", 10, definition())
    store.activate(draft["id"])
    paused = store.set_status("sub-1", "paused", reason="人工暂停")
    assert paused["status"] == "paused"
    resumed = store.set_status("sub-1", "active")
    assert resumed["status"] == "active"
    cancelled = store.set_status("sub-1", "cancelled", reason="目标已终止")
    assert cancelled["status"] == "cancelled"
    with pytest.raises(ProductionLineError, match="不能从 cancelled 转换为 active"):
        store.set_status("sub-1", "active")


def test_current_production_line_version_has_restrict_foreign_key(conn):
    foreign_keys = conn.execute("PRAGMA foreign_key_list(production_lines)").fetchall()
    assert any(
        row["from"] == "current_version_id"
        and row["table"] == "production_line_versions"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in foreign_keys
    )


def test_activation_rejects_stale_production_line_draft(conn):
    store = ProductionLineStore(conn)
    first = store.create_draft("sub-1", 10, definition())
    store.activate(first["id"])

    stale_a = store.create_draft(
        "sub-1", 10, definition(), based_on_version_id=first["id"],
        change_note="并发编辑 A",
    )
    stale_b = store.create_draft(
        "sub-1", 10, definition(), based_on_version_id=first["id"],
        change_note="并发编辑 B",
    )

    store.activate(stale_a["id"])
    with pytest.raises(ProductionLineError, match="基准版本已过期"):
        store.activate(stale_b["id"])


def test_create_draft_rejects_outdated_explicit_base_version(conn):
    store = ProductionLineStore(conn)
    first = store.create_draft("sub-1", 10, definition())
    store.activate(first["id"])
    latest = store.create_draft("sub-1", 10, definition(), based_on_version_id=first["id"])
    store.activate(latest["id"])

    with pytest.raises(ProductionLineError, match="基准版本已过期"):
        store.create_draft("sub-1", 10, definition(), based_on_version_id=first["id"])


def test_approving_new_version_does_not_resume_paused_production_line(conn):
    store = ProductionLineStore(conn)
    first = store.create_draft("sub-1", 10, definition())
    store.activate(first["id"])
    store.set_status("sub-1", "paused", reason="人工暂停")

    revision = store.create_draft(
        "sub-1", 10, definition(), based_on_version_id=first["id"],
        change_note="暂停期间修订",
    )
    store.activate(revision["id"])

    line = store.get_for_subplan("sub-1")
    assert line["status"] == "paused"
    assert line["current_version_id"] == revision["id"]
    assert line["pause_reason"] == "人工暂停"
    # Parent authorization is valid; only this explicit transition resumes it.
    resumed = store.set_status("sub-1", "active")
    assert resumed["status"] == "active"
