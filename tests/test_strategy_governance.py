from __future__ import annotations

import sqlite3

from src.core import strategy_governance


def test_strategy_fingerprint_ignores_production_history_but_tracks_resource_evidence(monkeypatch):
    north_star = {
        "id": 7, "version_no": 3,
        "snapshot": {"positioning": "专业内容服务"},
    }
    baseline = {
        "resources": [
            {"category": "business_profile", "title": "业务档案", "status": "confirmed", "updated_at": "2026-11-01"},
            {"category": "historical_output", "title": "历史内容", "status": "confirmed", "details": {"content_count": 100}},
            {"category": "historical_performance", "title": "历史表现", "status": "confirmed", "details": {"metric_rows": 20}},
            {"category": "historical_content", "title": "历史内容素材", "status": "confirmed", "details": {"content_asset_count": 50}},
        ],
        "gaps": [
            {"category": "historical_output", "title": "无历史产出", "status": "open"},
        ],
    }
    monkeypatch.setattr("src.core.north_star_repository.get_current", lambda _conn: north_star)
    monkeypatch.setattr("src.core.resource_baseline_repository.build_resource_baseline", lambda _conn: baseline)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE account_group_north_star_versions (id INTEGER)")
    first = strategy_governance.collect_strategy_governance(conn)

    baseline["resources"][0]["updated_at"] = "2026-11-05T12:00:00+00:00"
    baseline["resources"][1]["details"]["content_count"] = 101
    baseline["resources"][2]["details"]["metric_rows"] = 21
    baseline["resources"][3]["details"]["content_asset_count"] = 51
    second = strategy_governance.collect_strategy_governance(conn)
    assert strategy_governance.strategy_governance_fingerprint(first) == strategy_governance.strategy_governance_fingerprint(second)

    baseline["resources"][0]["status"] = "pending"
    third = strategy_governance.collect_strategy_governance(conn)
    conn.close()
    assert strategy_governance.strategy_governance_fingerprint(second) != strategy_governance.strategy_governance_fingerprint(third)


def test_strategy_fingerprint_tracks_north_star_version(monkeypatch):
    current = {"id": 1, "version_no": 1, "snapshot": {"positioning": "旧定位"}}
    monkeypatch.setattr("src.core.north_star_repository.get_current", lambda _conn: current)
    monkeypatch.setattr("src.core.resource_baseline_repository.build_resource_baseline", lambda _conn: {"resources": [], "gaps": []})
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE account_group_north_star_versions (id INTEGER)")
    first = strategy_governance.collect_strategy_governance(conn)
    current.update({"id": 2, "version_no": 2, "snapshot": {"positioning": "新定位"}})
    second = strategy_governance.collect_strategy_governance(conn)
    conn.close()
    assert strategy_governance.strategy_governance_fingerprint(first) != strategy_governance.strategy_governance_fingerprint(second)



def test_child_strategy_context_uses_master_bound_versions_not_current(monkeypatch):
    conn = sqlite3.connect(":memory:")
    baseline = {"snapshot": {"resources": [{"category": "business_profile", "status": "confirmed"}]}, "captured_at": "2026-11-05T10:00:00Z", "captured_by": "test"}
    seen = {}
    def read_bound_baseline(_conn, master_id):
        seen["master_id"] = master_id
        return baseline
    monkeypatch.setattr("src.core.resource_baseline_repository.get_plan_resource_baseline", read_bound_baseline)
    master = {
        "id": 22,
        "north_star_version_id": 5,
        "north_star_snapshot_json": '{"positioning":"绑定的历史定位"}',
    }
    result = strategy_governance.load_master_strategy_context(conn, master)
    conn.close()
    assert seen["master_id"] == 22
    assert result["north_star"] == {"version_id": 5, "snapshot": {"positioning": "绑定的历史定位"}}
    assert result["resource_baseline"] == baseline
    assert result["resource_baseline_available"] is True



def test_strategy_governance_is_scoped_to_the_supplied_tenant_connection(monkeypatch):
    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    for conn, version, label in [(first, 1, "tenant-a"), (second, 9, "tenant-b")]:
        conn.execute("CREATE TABLE account_group_north_star_versions (id INTEGER)")
        conn.execute("CREATE TABLE tenant_marker (label TEXT)")
        conn.execute("INSERT INTO tenant_marker VALUES (?)", (label,))
        conn.execute("INSERT INTO account_group_north_star_versions VALUES (?)", (version,))

    def read_north_star(conn):
        row = conn.execute("SELECT id FROM account_group_north_star_versions").fetchone()
        label = conn.execute("SELECT label FROM tenant_marker").fetchone()[0]
        return {"id": row[0], "version_no": row[0], "snapshot": {"positioning": label}}

    def read_baseline(conn):
        label = conn.execute("SELECT label FROM tenant_marker").fetchone()[0]
        return {"resources": [{"category": "business_profile", "status": "confirmed", "details": {"tenant": label}}], "gaps": []}

    monkeypatch.setattr("src.core.north_star_repository.get_current", read_north_star)
    monkeypatch.setattr("src.core.resource_baseline_repository.build_resource_baseline", read_baseline)
    first_context = strategy_governance.collect_strategy_governance(first)
    second_context = strategy_governance.collect_strategy_governance(second)
    first.close()
    second.close()

    assert first_context["north_star"]["snapshot"]["positioning"] == "tenant-a"
    assert second_context["north_star"]["snapshot"]["positioning"] == "tenant-b"
    assert strategy_governance.strategy_governance_fingerprint(first_context) != strategy_governance.strategy_governance_fingerprint(second_context)
