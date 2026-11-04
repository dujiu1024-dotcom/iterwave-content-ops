"""T8-B: exercise the production round API through the persistent queue and worker."""
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
        "cfg": cfg, "conn": conn, "runtime_tenant_id": "t8b-worker-tenant",
        "sm_round": RoundStateMachine(conn, cfg=cfg),
        "sm_draft": DraftStateMachine(conn, cfg=cfg),
    })
    api_main._task_queue = queue
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *a, **k: {})
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
            "cfg": worker_cfg, "conn": conn,
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
        assert queued is not None and queued.status == "queued"
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
        assert completed.result == {"round_id": round_id, "final_status": "completed"}
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved_context)
        api_main._task_queue = saved_queue


def test_round_run_api_worker_executes_real_round_executor(
    cfg, conn, seed, test_auth, tmp_path, monkeypatch,
):
    """The real RoundExecutor lifecycle writes a content and draft through the worker."""
    from pathlib import Path

    from src.adapters.base import Platform
    from src.core import round_executor as executor_module
    from src.core.guard.ultimate_goal_checker import CheckOutcome, CheckResult
    from src.core.round_executor import RoundExecutor

    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    round_id = seed.round(status="created")
    saved_context = dict(api_main._g_ctx)
    saved_queue = api_main._task_queue
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "cfg": cfg, "conn": conn, "runtime_tenant_id": "t8b-real-executor-tenant",
        "sm_round": RoundStateMachine(conn, cfg=cfg),
        "sm_draft": DraftStateMachine(conn, cfg=cfg),
    })
    api_main._task_queue = queue

    worker_cfg = cfg.model_copy(update={
        "paths": cfg.paths.model_copy(update={
            "data_dir": str(tmp_path / "tenant" / "data"),
            "output_dir": str(tmp_path / "tenant" / "output"),
        }),
    })
    topic = SimpleNamespace(
        id="t8b-real-worker-topic",
        title="真实执行器图文生产验收",
        summary="通过真实 RoundExecutor 验证 Worker 写入内容与草稿",
        dimension="产品体验",
    )
    scored = SimpleNamespace(
        topic=topic, total_score=90.0, score_breakdown={}, is_exploration=False,
    )

    class PassingGuard:
        def check_content(self, *_args, **_kwargs):
            return CheckOutcome(CheckResult.PASS, "deterministic test pass")

    def fake_generate_card_set(*, output_dir, **_kwargs):
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        image = path / "t8b-real-worker-card.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"deterministic-image")
        return [str(image)], []

    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *a, **k: {})
    from src.core import task_snapshot
    monkeypatch.setattr(task_snapshot, "validate_execution_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(executor_module, "load_pool", lambda *_: ([topic], {}))
    monkeypatch.setattr(executor_module, "_llm_available", lambda _cfg: True)
    monkeypatch.setattr(
        executor_module, "generate_copy_content",
        lambda *_args, **_kwargs: (
            "真实执行器生成标题", "这是一段用于验收真实生产执行器的图文正文。", ["产品体验"],
        ),
    )
    monkeypatch.setattr(
        executor_module.PlanVersionManager, "load_plan",
        lambda _self: (
            {"topic_count_per_platform": 1, "enabled_platforms": ["xhs"],
             "dimension_allocation": {}, "per_platform": {}},
            "t8b-test-signature", None, "plan_settings",
        ),
    )
    monkeypatch.setattr(executor_module, "effective_persona", lambda *_: {"persona_keywords": ["产品"]})
    monkeypatch.setattr(RoundExecutor, "_select_topics", lambda self, *_: [scored])
    monkeypatch.setattr(RoundExecutor, "_resolve_platforms", lambda self, *_: [Platform.XHS])
    monkeypatch.setattr(RoundExecutor, "_record_plan_signature", lambda *args, **kwargs: None)
    monkeypatch.setattr(RoundExecutor, "_upsert_topic_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(RoundExecutor, "_platform_broken", lambda *args, **kwargs: False)
    monkeypatch.setattr(RoundExecutor, "_image_settings", lambda self: (
        {"model": "deterministic-image", "fallback_enabled": False}, "",
    ))
    monkeypatch.setattr(
        "src.core.ai_card_generation.generate_ai_card_set", fake_generate_card_set,
    )
    monkeypatch.setattr(
        "src.core.visual_ocr.load_ocr_report",
        lambda _paths: {"status": "passed", "gate": "passed", "reason": None},
    )
    monkeypatch.setattr(
        "src.core.visual_review.assess_visual_pages",
        lambda _paths: {"status": "not_evaluated", "reason": "deterministic test", "score": None},
    )

    def build_tenant_context(tenant_id):
        assert tenant_id == "t8b-real-executor-tenant"
        executor = RoundExecutor(
            worker_cfg, conn, scoring=None, guard=PassingGuard(), adapters={},
            sm_round=RoundStateMachine(conn, cfg=worker_cfg),
            sm_draft=DraftStateMachine(conn, cfg=worker_cfg),
        )
        return {
            "cfg": worker_cfg, "conn": conn, "executor": executor,
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
        task_id = response.json()["task_id"]
        queued = queue.get(task_id)
        assert queued is not None and queued.status == "queued"
        assert queued.tenant_id == "t8b-real-executor-tenant"

        stop = asyncio.Event()

        class StopAfterOneWorker(GlobalTaskWorker):
            async def _execute(self, task, **kwargs):
                await super()._execute(task, **kwargs)
                stop.set()

        worker = StopAfterOneWorker(
            queue, build_tenant_context, poll_interval_seconds=0.01,
            lease_seconds=30, heartbeat_seconds=1, reconcile_terminal_rounds=False,
        )
        asyncio.run(worker.run(stop))

        completed = queue.get(task_id)
        assert completed is not None and completed.status == "succeeded"
        content = conn.execute(
            "SELECT id,title,body,visual_status FROM contents WHERE round_id=?",
            (round_id,),
        ).fetchone()
        assert content is not None
        assert content["title"] == "真实执行器生成标题"
        assert content["body"].startswith("这是一段用于验收真实生产执行器")
        assert content["visual_status"] == "generated"
        draft = conn.execute(
            "SELECT status FROM drafts WHERE content_id=?", (content["id"],)
        ).fetchone()
        assert draft is not None and draft["status"] == "draft_pending"
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved_context)
        api_main._task_queue = saved_queue
