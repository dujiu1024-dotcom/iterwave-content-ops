"""T8-B API -> persistent queue -> real GlobalTaskWorker dispatch acceptance.

The executor is deterministic here: this test isolates the production API/queue/worker
boundary. Real RoundExecutor artifact generation and worker-failure isolation are covered
by follow-up tests in this acceptance branch, not simulated by this test.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.task_queue import GlobalTaskQueue
from src.core.task_worker import GlobalTaskWorker
from src.core.state_machine import DraftStateMachine, RoundStateMachine


def test_round_run_api_is_consumed_by_global_worker(
    cfg, conn, seed, test_auth, tmp_path, monkeypatch,
):
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    round_id = seed.round(status="created")

    saved_context = dict(api_main._g_ctx)
    saved_queue = api_main._task_queue
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "cfg": cfg,
        "conn": conn,
        "runtime_tenant_id": "t8b-worker-tenant",
        "sm_round": RoundStateMachine(conn, cfg=cfg),
        "sm_draft": DraftStateMachine(conn, cfg=cfg),
    })
    api_main._task_queue = queue
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *a, **k: {})
    # The queue/worker contract is under test; snapshot validation has dedicated tests.
    from src.core import task_snapshot
    monkeypatch.setattr(task_snapshot, "validate_execution_snapshot", lambda *a, **k: None)

    observed = {}
    stop = asyncio.Event()

    class DeterministicExecutor:
        async def run(self, received_round_id, **kwargs):
            observed["round_id"] = received_round_id
            observed["kwargs"] = kwargs
            stop.set()
            return {"round_id": received_round_id, "final_status": "completed"}

    worker_cfg = cfg.model_copy(update={
        "paths": cfg.paths.model_copy(update={
            "data_dir": str(tmp_path / "tenant" / "data"),
        }),
    })

    def build_tenant_context(tenant_id):
        assert tenant_id == "t8b-worker-tenant"
        return {
            "cfg": worker_cfg,
            "conn": conn,
            "executor": DeterministicExecutor(),
            "scheduler": SimpleNamespace(shutdown=lambda: None),
            "notifier": None,
        }

    try:
        client = test_auth(TestClient(api_main._build_app(), base_url="http://127.0.0.1"))
        response = client.post(
            f"/api/rounds/{round_id}/run",
            json={"topic_count": 1, "platforms": ["xhs"], "save_drafts": False},
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["queued"] is True
        assert payload["status"] == "queued"

        queued = queue.get(payload["task_id"])
        assert queued is not None
        assert queued.status == "queued"
        assert queued.tenant_id == "t8b-worker-tenant"
        assert queued.payload["round_id"] == round_id

        worker = GlobalTaskWorker(
            queue, build_tenant_context, poll_interval_seconds=0.01,
            lease_seconds=30, heartbeat_seconds=1, reconcile_terminal_rounds=False,
        )
        asyncio.run(worker.run(stop))

        completed = queue.get(payload["task_id"])
        assert completed is not None and completed.status == "succeeded"
        assert observed["round_id"] == round_id
        assert completed.result == {
            "round_id": round_id, "final_status": "completed",
        }
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved_context)
        api_main._task_queue = saved_queue
