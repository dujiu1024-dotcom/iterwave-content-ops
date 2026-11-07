from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.tenant_registry import TenantRegistry


def _seed_tenant_db(db_path: Path, title: str, platform: str = "xhs") -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 新建账号组会自动建库并跑全量迁移；这里换成最小只读聚合 schema。
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE contents (id TEXT PRIMARY KEY, platform TEXT, created_at TEXT);
        CREATE TABLE drafts (id TEXT PRIMARY KEY, platform TEXT, status TEXT);
        CREATE TABLE media_assets (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE media_asset_usages (id INTEGER PRIMARY KEY);
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            biz_id TEXT,
            status TEXT NOT NULL,
            error_msg TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT
        );
    """)
    now = datetime.now().isoformat(sep=" ")
    conn.execute("INSERT INTO contents VALUES (?, ?, ?)", (title, platform, now))
    conn.execute(
        "INSERT INTO task_runs(task_type, biz_id, status, started_at, finished_at, created_at) "
        "VALUES ('round_pipeline', ?, 'success', ?, ?, ?)",
        ("round-" + title, now, now, now),
    )
    conn.execute("INSERT INTO drafts VALUES (?, ?, 'draft_saved')", ("draft-" + title, platform))
    conn.execute("INSERT INTO media_assets VALUES (1, 'active')")
    conn.execute("INSERT INTO media_asset_usages VALUES (1)")
    conn.commit()
    conn.close()


def test_global_monitor_aggregates_tenant_dbs_read_only(tmp_path, test_auth):
    registry = TenantRegistry(tmp_path / "workspace")
    registry.initialize()
    registry.create("brand_a", "品牌 A")
    for tenant_id in ("default", "brand_a"):
        paths = registry.paths(tenant_id, create=True)
        _seed_tenant_db(paths.db_file, "content-" + tenant_id, "xhs" if tenant_id == "default" else "douyin")

    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "tenant_registry": registry,
        "runtime_tenant_id": "default",
    })
    try:
        client = test_auth(TestClient(api_main._build_app(), base_url="http://127.0.0.1"))
        response = client.get("/api/global-monitor?days=30")
        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["tenants"] == 2
        assert body["totals"]["available_tenants"] == 2
        assert body["totals"]["contents_all"] == 2
        assert body["totals"]["drafts_saved"] == 2
        assert body["totals"]["media_active"] == 2
        assert body["platform_totals"]["xhs"]["contents"] == 1
        assert body["platform_totals"]["douyin"]["contents"] == 1
        for tenant in body["tenants"]:
            assert tenant["recent_execution"]["task_type"] == "round_pipeline"
            assert tenant["recent_execution"]["status"] == "success"
        assert body["account_count_note"]
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


def test_global_todos_sanitize_task_and_media_generation_errors(tmp_path, test_auth):
    registry = TenantRegistry(tmp_path / "workspace")
    registry.initialize()
    paths = registry.paths("default", create=True)
    conn = sqlite3.connect(paths.db_file)
    conn.executescript("""
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY, task_type TEXT, status TEXT, platform TEXT,
            error_msg TEXT, finished_at TEXT, started_at TEXT, created_at TEXT
        );
        CREATE TABLE media_generation_jobs (
            id INTEGER PRIMARY KEY, error_message TEXT, created_at TEXT, status TEXT
        );
    """)
    now = datetime.now().isoformat(sep=" ")
    internal_error = "sqlite3.OperationalError: unable to open /home/ubuntu/iterwave/data/private.db\\nTraceback (most recent call last):"
    conn.execute(
        "INSERT INTO task_runs(task_type,status,platform,error_msg,created_at) VALUES (?,?,?,?,?)",
        ("round_pipeline", "failed", "xhs", internal_error, now),
    )
    conn.execute(
        "INSERT INTO media_generation_jobs(error_message,created_at,status) VALUES (?,?,?)",
        (internal_error, now, "failed"),
    )
    conn.commit()
    conn.close()

    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({"tenant_registry": registry, "runtime_tenant_id": "default"})
    try:
        client = test_auth(TestClient(api_main._build_app(), base_url="http://127.0.0.1"))
        task_response = client.get("/api/todos?source=task_run")
        media_response = client.get("/api/todos?source=media_generation")
        assert task_response.status_code == 200
        assert media_response.status_code == 200
        for response in (task_response, media_response):
            assert "/home/ubuntu" not in response.text
            assert "sqlite3.OperationalError" not in response.text
            assert "Traceback" not in response.text
        assert "任务执行失败，请进入任务追踪查看详情。" in task_response.text
        assert "AI 配图生成失败，请检查图片生成配置后重试。" in media_response.text
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)
