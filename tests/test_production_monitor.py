from __future__ import annotations

import sqlite3
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.subplans import register_subplan_routes


def test_production_monitor_exposes_plan_lineage_and_consistent_counts(tmp_path):
    db = tmp_path / "tenant.db"
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE master_plan_versions (
            id INTEGER PRIMARY KEY, version_no INTEGER, plan_signature TEXT,
            status TEXT, created_at TEXT, effective_at TEXT
        );
        CREATE TABLE sub_plans (
            id TEXT PRIMARY KEY, platform TEXT, title TEXT, status TEXT,
            master_plan_version_id INTEGER, current_version_id INTEGER,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE sub_plan_versions (
            id INTEGER PRIMARY KEY, version_no INTEGER, status TEXT
        );
        CREATE TABLE sub_plan_stages (
            id INTEGER PRIMARY KEY, sub_plan_version_id INTEGER, stage_key TEXT, stage_order INTEGER,
            title TEXT, status TEXT, target_count INTEGER, updated_at TEXT,
            dependencies_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE spiral_rounds (
            id INTEGER PRIMARY KEY, round_number INTEGER, status TEXT, created_at TEXT,
            trigger_source TEXT, plan_attribution_status TEXT, sub_plan_id TEXT,
            sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER
        );
        CREATE TABLE contents (
            id TEXT PRIMARY KEY, title TEXT, platform TEXT, created_at TEXT,
            round_id INTEGER, review_rejected INTEGER
        );
        CREATE TABLE drafts (
            id TEXT PRIMARY KEY, content_id TEXT, status TEXT, user_confirmed_at TEXT,
            review_status TEXT NOT NULL DEFAULT 'pending', publish_status TEXT NOT NULL DEFAULT 'not_published'
        );
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY, task_type TEXT, biz_id TEXT, status TEXT
        );
        CREATE TABLE production_tasks (
            id TEXT PRIMARY KEY, round_id INTEGER, sub_plan_id TEXT,
            sub_plan_version_id INTEGER, strategy_step_id TEXT, status TEXT
        );
        CREATE TABLE user_override (
            id INTEGER PRIMARY KEY, round_id INTEGER, override_field TEXT,
            original_value TEXT, overridden_at TEXT
        );
        CREATE TABLE metrics (id INTEGER PRIMARY KEY, draft_id TEXT, content_id TEXT, fetched_at TEXT, fetch_status TEXT, views INTEGER, likes INTEGER, collects INTEGER, comments INTEGER, shares INTEGER, new_followers INTEGER);
        CREATE VIEW sub_plan_stage_publish_stats AS
            SELECT id AS stage_id, sub_plan_version_id, 0 AS confirmed_published_count
            FROM sub_plan_stages;
        INSERT INTO master_plan_versions VALUES (1, 4, 'sig', 'effective', '2026-11-01', '2026-11-01');
        INSERT INTO sub_plans VALUES ('sp-xhs', 'xhs', '小红书策略', 'active', 1, 2, '2026-11-01', '2026-11-03');
        INSERT INTO sub_plan_versions VALUES (2, 1, 'effective');
        INSERT INTO sub_plan_stages VALUES (3, 2, 'stage-a', 1, '建立认知', 'in_progress', 5, '2026-11-03', '["stage-prereq"]');
        INSERT INTO spiral_rounds VALUES (7, 2, 'awaiting_publish', '2026-11-03T08:00:00+00:00',
            'manual', 'resolved', 'sp-xhs', 2, 3);
        INSERT INTO contents VALUES ('content-1', '测试内容', 'xhs', '2026-11-03', 7, 1);
        INSERT INTO drafts VALUES ('draft-1', 'content-1', 'draft_saved', NULL, 'rejected', 'failed');
        INSERT INTO contents VALUES ('content-2', '已通过内容', 'xhs', '2026-11-03', 7, 0);
        INSERT INTO drafts VALUES ('draft-2', 'content-2', 'draft_saved', NULL, 'approved', 'not_published');
        INSERT INTO user_override VALUES (1, 7, 'review_approve', '{"content_id":"content-2"}', '2026-11-03');
        INSERT INTO task_runs VALUES (1, 'round_pipeline', '7', 'success');
        INSERT INTO task_runs VALUES (2, 'round_pipeline', '7', 'success');
        INSERT INTO task_runs VALUES (3, 'round_pipeline', '7', 'success');
        INSERT INTO production_tasks VALUES ('pt-1', 7, 'sp-xhs', 2, 'stage-a', 'pending');
        INSERT INTO production_tasks VALUES ('pt-2', 7, 'sp-xhs', 2, 'stage-a', 'succeeded');
        INSERT INTO metrics VALUES (1, 'draft-1', 'content-1', '2026-11-02', 'success', 50, 4, 2, 1, 0, 0);
        INSERT INTO metrics VALUES (2, 'draft-1', 'content-1', '2026-11-03', 'success', 100, 8, 3, 2, 1, 1);
        INSERT INTO metrics VALUES (3, 'draft-1', 'content-1', '2026-11-04', 'failed', 999, 99, 99, 99, 99, 99);
    """)
    app = FastAPI()
    register_subplan_routes(app, lambda: {"conn": conn})
    try:
        response = TestClient(app).get("/api/production-monitor")
        assert response.status_code == 200
        body = response.json()
        assert body["master_plan"]["id"] == 1
        assert body["totals"]["subplans"] == 1
        assert body["totals"]["stages"] == 1
        assert body["totals"]["rounds"] == 1
        assert body["totals"]["production_tasks"] == 2
        assert body["totals"]["contents"] == 2
        assert body["totals"]["target_contents"] == 5
        assert body["totals"]["content_progress_percent"] == 40.0
        assert body["totals"]["drafts"] == 2
        assert body["totals"]["review_rejected"] == 1
        assert body["totals"]["review_approved"] == 1
        assert body["totals"]["review_pending"] == 0
        assert body["totals"]["published"] == 0
        assert body["effect_summary"]["views"] == 100
        assert body["effect_summary"]["likes"] == 8
        stage = body["platforms"][0]["stages"][0]
        assert stage["round_status_counts"]["awaiting_publish"] == 1
        assert stage["production_task_count"] == 2
        assert stage["production_task_status_counts"] == {"pending": 1, "succeeded": 1}
        assert stage["dependencies"] == ["stage-prereq"]
        assert stage["dependencies_available"] is True
        assert body["limitations"]["production_task_source"] == "production_tasks"
        assert body["limitations"]["task_model_available"] is True
        assert stage["rounds"][0]["id"] == 7
        assert {item["id"] for item in stage["contents"]} == {"content-1", "content-2"}
        assert stage["review_approved_count"] == 1
        assert not any(item["code"] == "strategy_step_dependencies_unavailable"
                       for item in body["consistency_checks"])
    finally:
        conn.close()
