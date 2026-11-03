"""Business profile repository and API contract tests."""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.core.business_profile_repository import (
    archive_item, get_profile, get_snapshot, list_items, list_versions,
    plan_snapshot, save_item, save_profile,
)


def setup_schema(conn):
    conn.executescript("""
    CREATE TABLE business_profiles (
      id INTEGER PRIMARY KEY, business_name TEXT NOT NULL DEFAULT '',
      introduction TEXT NOT NULL DEFAULT '', industry TEXT NOT NULL DEFAULT '',
      business_model TEXT NOT NULL DEFAULT '', business_stage TEXT NOT NULL DEFAULT '',
      operating_goal TEXT NOT NULL DEFAULT '', field_evidence_json TEXT NOT NULL DEFAULT '{}',
      version_no INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
    );
    CREATE TABLE business_catalog_items (
      id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, name TEXT NOT NULL,
      details_json TEXT NOT NULL DEFAULT '{}', field_evidence_json TEXT NOT NULL DEFAULT '{}',
      is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE business_profile_versions (
      version_no INTEGER PRIMARY KEY, snapshot_json TEXT NOT NULL,
      change_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT NOT NULL
    );
    CREATE TABLE master_plan_business_snapshots (
      master_plan_version_id INTEGER PRIMARY KEY, business_profile_version_no INTEGER NOT NULL,
      snapshot_json TEXT NOT NULL, captured_at TEXT NOT NULL
    );
    """)
    return conn


@pytest.fixture
def conn():
    connection = setup_schema(sqlite3.connect(":memory:"))
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def test_profile_save_versions_and_field_provenance(conn):
    saved = save_profile(conn, {
        "business_name": "示例业务", "industry": "教育",
        "field_evidence": {"industry": {"status": "external_source", "source": "公司官网"}},
    })
    assert saved["version_no"] == 1
    assert saved["field_evidence"]["business_name"]["status"] == "user_confirmed"
    assert saved["field_evidence"]["industry"]["status"] == "external_source"
    save_profile(conn, {"operating_goal": "提高转化", "change_note": "新增经营目标"})
    assert [v["version_no"] for v in list_versions(conn)] == [2, 1]


def test_external_evidence_requires_source(conn):
    with pytest.raises(ValueError, match="来源说明"):
        save_profile(conn, {
            "industry": "教育",
            "field_evidence": {"industry": {"status": "external_source"}},
        })


def test_catalog_supports_multiple_products_and_services(conn):
    p = save_item(conn, "product", {
        "name": "课程A", "details": {"selling_points": ["实操"], "price_range": "99-199"},
    })
    save_item(conn, "product", {"name": "课程B", "details": {"use_cases": ["入门"]}})
    save_item(conn, "service", {"name": "咨询服务", "details": {"delivery": "线上"}})
    assert len(list_items(conn, "product")) == 2
    assert len(list_items(conn, "service")) == 1
    assert p["field_evidence"]["selling_points"]["status"] == "user_confirmed"
    assert len(list_versions(conn)) == 4
    assert get_profile(conn)["version_no"] == 4


def test_ai_inferred_data_is_not_automatically_confirmed(conn):
    item = save_item(conn, "product", {
        "name": "待确认产品", "details": {"selling_points": ["AI提取卖点"]},
        "field_evidence": {"selling_points": {"status": "ai_inferred", "source": "用户原始文本"}},
    })
    assert item["field_evidence"]["selling_points"]["status"] == "ai_inferred"


def test_archive_preserves_history_and_snapshot(conn):
    item = save_item(conn, "market", {"name": "竞品A", "details": {"positioning": "低价"}})
    assert archive_item(conn, item["id"])
    assert list_items(conn, "market") == []
    assert get_snapshot(conn)["items"] == []


def test_profile_unknown_fields_are_rejected(conn):
    with pytest.raises(ValueError, match="不支持"):
        save_profile(conn, {"secret_token": "no"})


def test_migration_captures_structured_snapshot_when_master_plan_activates():
    from pathlib import Path

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE master_plan_versions (
          id INTEGER PRIMARY KEY, status TEXT NOT NULL, effective_at TEXT,
          reviewed_at TEXT, created_at TEXT NOT NULL
        )
    """)
    migration = Path(__file__).resolve().parents[1] / "migrations" / "143_business_analysis_foundation.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO master_plan_versions(id,status,created_at) VALUES(1,'draft','2026-11-03T00:00:00Z')"
    )
    save_profile(conn, {"business_name": "测试业务", "industry": "教育"})
    save_item(conn, "product", {"name": "测试产品", "details": {"selling_points": ["真实体验"]}})
    conn.execute(
        "UPDATE master_plan_versions SET status='effective',effective_at='2026-11-03T01:00:00Z' WHERE id=1"
    )
    row = conn.execute(
        "SELECT business_profile_version_no,snapshot_json FROM master_plan_business_snapshots WHERE master_plan_version_id=1"
    ).fetchone()
    snapshot = json.loads(row["snapshot_json"])
    assert row["business_profile_version_no"] == 2
    assert snapshot["profile"]["business_name"] == "测试业务"
    assert snapshot["items"][0]["name"] == "测试产品"
    conn.close()


def test_manual_catalog_fields_are_marked_user_confirmed(conn):
    item = save_item(conn, "audience", {
        "name": "初创企业主", "details": {"pain_points": ["获客成本高"]},
    })
    assert item["field_evidence"]["pain_points"]["status"] == "user_confirmed"
