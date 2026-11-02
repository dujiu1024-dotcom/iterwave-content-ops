import asyncio

from src.core.scheduler import SchedulerEngine
from src.core.task_queue import GlobalTaskQueue
from src.core.task_worker import GlobalTaskWorker


def test_autogrow_scheduler_propagates_failed_execution_report():
    expected = {
        "can_start": True,
        "action": "failed",
        "reason": "内容生成失败",
        "round_id": 81,
    }

    class FakeAutogrow:
        async def tick(self):
            return expected

    engine = object.__new__(SchedulerEngine)
    engine.autogrow = FakeAutogrow()

    actual = asyncio.run(engine._job_autogrow_next_round())

    assert actual is not expected
    assert actual["reason"] == expected["reason"]
    assert actual["action"] == "failed"
    assert actual["final_status"] == "failed"
    assert actual["errors"] == ["内容生成失败"]


def test_autogrow_scheduler_rejects_non_dict_execution_report():
    class FakeAutogrow:
        async def tick(self):
            return None

    engine = object.__new__(SchedulerEngine)
    engine.autogrow = FakeAutogrow()

    actual = asyncio.run(engine._job_autogrow_next_round())

    assert actual["final_status"] == "failed"
    assert "无效执行报告" in actual["errors"][0]


def test_autogrow_scheduler_reports_disabled_controller_as_skipped():
    engine = object.__new__(SchedulerEngine)
    engine.autogrow = None

    actual = asyncio.run(engine._job_autogrow_next_round())

    assert actual["final_status"] == "skipped"


def test_worker_marks_autogrow_action_failed_as_failed_queue_task(tmp_path):
    queue = GlobalTaskQueue(tmp_path / "system.db")
    queue.initialize()
    task, _ = queue.enqueue(
        task_type="scheduler_job",
        tenant_id="tenant-a",
        aggregate_id="_job_autogrow_next_round",
        payload={"method": "_job_autogrow_next_round", "args": []},
    )
    claimed = queue.claim_next("worker-autogrow-failure")
    assert claimed is not None

    class FakeAutogrow:
        async def tick(self):
            return {
                "can_start": True,
                "action": "failed",
                "reason": "生成草稿时模型服务异常",
            }

    scheduler = object.__new__(SchedulerEngine)
    scheduler.autogrow = FakeAutogrow()
    scheduler.shutdown = lambda: None
    context = {
        "cfg": type("Config", (), {
            "paths": type("Paths", (), {"data_dir": str(tmp_path / "tenant-a" / "data")})(),
            "resolve_path": lambda _self, value: value,
        })(),
        "scheduler": scheduler,
        "conn": None,
    }
    worker = GlobalTaskWorker(queue, lambda _tenant_id: context)
    worker.owner = "worker-autogrow-failure"

    asyncio.run(worker._execute(claimed))

    saved = queue.get(task.task_id)
    assert saved is not None
    assert saved.status == "failed"
    assert saved.error_code == "task_execution_failed"
    assert "生成草稿时模型服务异常" in (saved.error_message or "")
