import asyncio

from src.core.scheduler import SchedulerEngine


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

    assert actual is expected
    assert actual["action"] == "failed"


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
