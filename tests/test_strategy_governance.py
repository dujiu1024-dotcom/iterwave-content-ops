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
