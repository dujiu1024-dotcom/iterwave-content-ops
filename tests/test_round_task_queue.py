from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import src.api.main as api_main
from src.core.task_queue import GlobalTaskQueue


def _post_request(path: str) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": path,
        "headers": [], "query_string": b"", "server": ("testserver", 80),
        "client": ("testclient", 50000), "scheme": "http",
    })


def test_save_pending_drafts_queues_after_running_round_instead_of_deduping(tmp_path, monkeypatch):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    running, created = queue.enqueue(
        task_type="round_run", tenant_id="tenant-a", aggregate_id="7",
        payload={"round_id": 7, "operation": "run"},
    )
    assert created

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE spiral_rounds (id INTEGER PRIMARY KEY, status TEXT NOT NULL, sub_plan_id TEXT, sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER)")
    conn.execute("INSERT INTO spiral_rounds(id,status) VALUES(7,'awaiting_publish')")
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setitem(api_main._g_ctx, "conn", conn)
    monkeypatch.setitem(api_main._g_ctx, "runtime_tenant_id", "tenant-a")

    route = next(
        route for route in api_main.app.routes
        if getattr(route, "path", None) == "/api/rounds/{round_id}/save_pending"
        and "POST" in (getattr(route, "methods", None) or set())
    )
    response = asyncio.run(route.endpoint(7, _post_request("/api/rounds/7/save_pending")))

    pending = queue.find_latest(
        tenant_id="tenant-a", task_type="round_run",
        aggregate_id="7:save_pending_drafts",
    )
    assert response["created"] is True
    assert pending is not None and pending.status == "queued"
    assert pending.queue_order > running.queue_order
    assert api_main._round_task_active("tenant-a", 7) is True

    status_route = next(
        route for route in api_main.app.routes
        if getattr(route, "path", None) == "/api/rounds/{round_id}/run_status"
        and "GET" in (getattr(route, "methods", None) or set())
    )
    status = status_route.endpoint(7)
    assert status["task_type"] == "save_pending_drafts"
    assert status["queued"] is True
    conn.close()


def test_run_rejects_round_not_in_created_state(tmp_path, monkeypatch):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE spiral_rounds (id INTEGER PRIMARY KEY, status TEXT NOT NULL, sub_plan_id TEXT, sub_plan_version_id INTEGER, sub_plan_stage_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT, updated_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE sub_plans (id TEXT PRIMARY KEY, status TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO spiral_rounds(id, status) VALUES(7, 'awaiting_publish')"
    )
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *a, **k: {})
    monkeypatch.setitem(api_main._g_ctx, "conn", conn)
    monkeypatch.setitem(api_main._g_ctx, "runtime_tenant_id", "tenant-a")

    route = next(
        route for route in api_main.app.routes
        if getattr(route, "path", None) == "/api/rounds/{round_id}/run"
        and "POST" in (getattr(route, "methods", None) or set())
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.endpoint(7, api_main.RunRoundRequest(), _post_request("/api/rounds/7/run")))
    assert exc.value.status_code == 409
    assert "awaiting_publish" in str(exc.value.detail)
    assert queue.find_latest(tenant_id="tenant-a", task_type="round_run", aggregate_id="7") is None
    conn.close()



def test_automatic_round_enqueue_records_explicit_system_initiator(tmp_path, monkeypatch):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setattr(
        api_main, "_build_task_execution_snapshot",
        lambda tenant_id, round_id, parameters: {"tenant_id": tenant_id, "round_id": round_id},
    )

    task, created = api_main._enqueue_round_task(
        tenant_id="tenant-auto", round_id=91, topic_count=2,
        platforms=["xhs"], save_drafts=True,
    )

    assert created is True
    assert task.tenant_id == "tenant-auto"
    assert task.payload["initiating_actor"] == "system:automatic-round-scheduler"
    assert task.payload["execution_snapshot"] == {
        "tenant_id": "tenant-auto", "round_id": 91,
    }
