"""调度器缺陷回归：kb_scan 列对齐 / data_fetch 重试 / 熔断真实触发。"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from src.adapters.base import CircuitBreakerReason, DraftResult, Platform
from src.core.scheduler import SchedulerEngine
from src.core.timezone import business_now
from src.core.scoring.topic_eval import TopicItem


class _FakeKb:
    def read(self):
        return (
            [
                TopicItem(id="kt1", title="标题一", dimension="AI教育", source="kb"),
                TopicItem(id="kt2", title="标题二", dimension="AI教育", source="kb"),
            ],
            {"total": 2},
        )


def _engine(cfg, conn) -> SchedulerEngine:
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, kb_reader=_FakeKb())
    return eng


def test_kb_scan_inserts_without_missing_columns(conn: sqlite3.Connection, cfg) -> None:
    """topic_index 无 tags/summary 列，kb_scan 必须能成功插入（曾因列不存在必崩）。"""
    eng = _engine(cfg, conn)
    asyncio.run(eng._job_kb_scan())

    rows = conn.execute(
        "SELECT id, title, dimension, source FROM topic_index ORDER BY id"
    ).fetchall()
    assert [r["id"] for r in rows] == ["kt1", "kt2"]
    assert rows[0]["title"] == "标题一"


def test_publish_polling_fingerprint_tracks_every_image_and_matches_save_hash():
    import json
    from src.core.content_version import content_version_hash
    from src.core.scheduler import _draft_payload_content_hash

    images = ["/assets/cover.png", "/assets/card-2.png", "/assets/card-3.png"]
    saved_hash = content_version_hash("标题", "正文", ["运营"], images)

    def polling_hash(paths):
        return _draft_payload_content_hash(
            "标题", "正文", json.dumps(["运营"], ensure_ascii=False), json.dumps(paths),
        )

    assert polling_hash(images) == saved_hash
    # A change to a non-cover image must invalidate the saved-content fingerprint.
    assert polling_hash([images[0], "/assets/card-2-revised.png", images[2]]) != saved_hash
    # Ordering is part of the exact platform payload and must remain significant.
    assert polling_hash([images[1], images[0], images[2]]) != saved_hash
    assert polling_hash(images) == saved_hash



def test_metrics_retry_params_from_typed_config(cfg) -> None:
    """数据采集重试次数与间隔直接来自类型化配置。"""
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    assert eng._metrics_retry_params() == (3, 30)
    cfg.timeouts.metrics_retry_attempts = 5
    cfg.timeouts.metrics_retry_interval_minutes = 45
    assert eng._metrics_retry_params() == (5, 45)


def test_retry_job_id_includes_attempt(cfg) -> None:
    """重试注册必须区分 attempt，否则 replace_existing 会覆盖掉首次任务。"""
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.schedule_data_fetch("d1", datetime.now(), attempt=1)
    eng.schedule_data_fetch("d1", datetime.now(), attempt=2)
    ids = {j.id for j in eng.scheduler.get_jobs()}
    assert {"data_fetch_d1_a1", "data_fetch_d1_a2"} <= ids

def test_pending_poll_sequence_has_distinct_scheduler_job_id(cfg) -> None:
    """同一失败 attempt 的 pending 轮询必须有独立任务身份。"""
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.schedule_data_fetch("d1", datetime.now(), attempt=2, poll_sequence=1)
    assert "data_fetch_d1_a2_p1" in {j.id for j in eng.scheduler.get_jobs()}


def test_missing_metrics_adapter_records_failure_and_retries(cfg, conn, seed) -> None:
    from datetime import timedelta
    from src.adapters.base import FetchStatus
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at = ? WHERE id = ?",
        ((datetime.now() - timedelta(hours=48)).isoformat(), draft_id),
    )
    conn.commit()
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, sm_draft=DraftStateMachine(conn))

    # attempt 1：缺少适配器也要形成 failed metrics，且注册 attempt 2。
    asyncio.run(eng._job_data_fetch(draft_id, attempt=1))
    metrics = conn.execute(
        "SELECT fetch_status, raw_response, error_msg FROM metrics WHERE draft_id = ?", (draft_id,)
    ).fetchone()
    assert metrics["fetch_status"] == FetchStatus.FAILED.value
    assert "适配器未启用" in metrics["error_msg"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "user_published"
    assert f"data_fetch_{draft_id}_a2" in {j.id for j in eng.scheduler.get_jobs()}

    # attempt 2 继续重试；attempt 3 耗尽后明确结束，不再静默挂在 collecting。
    asyncio.run(eng._job_data_fetch(draft_id, attempt=2))
    assert f"data_fetch_{draft_id}_a3" in {j.id for j in eng.scheduler.get_jobs()}
    asyncio.run(eng._job_data_fetch(draft_id, attempt=3))
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "failed"
    error = conn.execute(
        "SELECT error_msg FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    assert "适配器未启用" in error



def test_data_fetch_retry_exhaustion_notification_includes_round_id(cfg, conn, seed):
    from datetime import timedelta
    from src.core.state_machine import DraftStateMachine

    cfg.timeouts.metrics_retry_attempts = 1
    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at = ? WHERE id = ?",
        ((datetime.now() - timedelta(hours=48)).isoformat(), draft_id),
    )
    conn.commit()

    class Notifier:
        def __init__(self):
            self.events = []

        def emit(self, event, message, **kwargs):
            self.events.append((event, message, kwargs))

    notifier = Notifier()
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(
        conn=conn, adapters={}, sm_draft=DraftStateMachine(conn),
        notifier=notifier,
    )
    report = asyncio.run(eng._job_data_fetch(draft_id, attempt=1))

    assert report["final_status"] == "failed"
    assert len(notifier.events) == 1
    event, _message, payload = notifier.events[0]
    assert event == "data_fetch_failed"
    assert payload["draft_id"] == draft_id
    assert payload["round_id"] == round_id

def test_backfill_requeues_when_only_failed_metrics_exist(cfg, conn, seed) -> None:
    """只存在 failed 采集记录不代表闭环完成；任务丢失后必须允许补登记。"""
    from datetime import timedelta

    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="data_fetching", fetch_status=None)
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    conn.execute("UPDATE drafts SET user_confirmed_at = ? WHERE id = ?", (old, draft_id))
    conn.execute(
        "INSERT INTO metrics (draft_id, content_id, platform, fetch_status, fetched_at, attempt) "
        "SELECT ?, id, 'xhs', 'failed', ?, 1 FROM contents WHERE round_id = ? LIMIT 1",
        (draft_id, datetime.now().isoformat(), round_id),
    )
    conn.commit()

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={})
    assert eng.backfill_data_fetch() == 1
    assert f"data_fetch_{draft_id}_a1" in {j.id for j in eng.scheduler.get_jobs()}
    assert eng.backfill_data_fetch() == 0


def test_data_fetch_pending_is_not_failure_or_retry_attempt(cfg, conn, seed) -> None:
    """平台指标仍未就绪属于 pending：不写失败记录、不消耗失败重试次数。"""
    from src.adapters.base import FetchStatus, MetricsSnapshot
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)

    class _PendingAdapter:
        async def fetch_metrics(self, *_args, **_kwargs):
            return MetricsSnapshot(
                platform=Platform.XHS,
                fetch_status=FetchStatus.PENDING,
                error_msg="作品数据尚未就绪",
            )

    scheduled = []
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(
        conn=conn,
        adapters={Platform.XHS: _PendingAdapter()},
        sm_draft=DraftStateMachine(conn),
    )
    eng.schedule_data_fetch = lambda draft_id, run_at, attempt=1, poll_sequence=0: scheduled.append(
        (draft_id, attempt, poll_sequence)
    )

    report = asyncio.run(eng._job_data_fetch(draft_id, attempt=2))

    assert report == {
        "final_status": "success",
        "errors": [],
        "attempt": 2,
        "pending": True,
        "retry_scheduled": True,
    }
    assert scheduled == [(draft_id, 2, 1)]
    assert conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE draft_id = ?", (draft_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "data_fetching"


def test_data_fetch_failure_schedules_retry(cfg, conn, seed) -> None:
    """采集失败 → 注册下一次 attempt（此前单次失败直接放弃，重试策略形同虚设）。"""
    from src.adapters.base import FetchStatus, MetricsSnapshot
    from src.core.state_machine import DraftStateMachine

    r = seed.round()
    draft_id = seed.draft(r, draft_status="user_published", fetch_status=None)

    # 当天已采集过 6 次（metrics.attempt=6）——不得被误当成作业重试次数
    conn.execute(
        "INSERT INTO metrics (draft_id, platform, fetch_status, fetched_at, attempt) "
        "VALUES (?, 'xhs', 'failed', ?, 6)",
        (draft_id, business_now(cfg).strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()

    class _Ad:
        async def fetch_metrics(self, *_a, **_k):
            return MetricsSnapshot(
                platform=Platform.XHS, fetch_status=FetchStatus.FAILED, error_msg="平台超时",
            )

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(
        conn=conn, adapters={Platform.XHS: _Ad()},
        sm_draft=DraftStateMachine(conn),
    )
    report = asyncio.run(eng._job_data_fetch(draft_id, attempt=1))
    assert report["final_status"] == "failed"
    assert report["retry_scheduled"] is True
    assert report["errors"] == ["xhs 数据采集失败（failed）"]

    persisted_error = conn.execute(
        "SELECT error_msg FROM metrics WHERE draft_id = ?", (draft_id,)
    ).fetchone()[0]
    assert persisted_error == "平台超时"

    ids = {j.id for j in eng.scheduler.get_jobs()}
    assert f"data_fetch_{draft_id}_a2" in ids
    assert f"data_fetch_{draft_id}_a1" not in ids


def test_round_executor_reports_signal(cfg, conn) -> None:
    """round_executor._report_circuit_signal 真的调 trigger（R6 熔断不再形同虚设）。"""
    from src.core.round_executor import RoundExecutor

    class _Cb:
        def __init__(self) -> None:
            self.calls = []

        def trigger(self, platform, reason) -> None:
            self.calls.append((platform, reason))

    cb = _Cb()
    ex = RoundExecutor(cfg, conn, None, None, {}, None, None, circuit_breaker=cb)
    ex._report_circuit_signal(
        Platform.DOUYIN, DraftResult(success=False, cb_signal=CircuitBreakerReason.RATE_LIMIT)
    )
    ex._report_circuit_signal(Platform.DOUYIN, DraftResult(success=False, login_required=True))
    # 无信号的失败不应熔断
    ex._report_circuit_signal(Platform.DOUYIN, DraftResult(success=False))
    assert cb.calls == [
        (Platform.DOUYIN, CircuitBreakerReason.RATE_LIMIT),
        (Platform.DOUYIN, CircuitBreakerReason.LOGIN_EXPIRED),
    ]


def test_daily_ops_skips_when_globally_paused(cfg, conn) -> None:
    """全局软暂停 → daily_ops 直接返回，不建 task_run、不调 ops（R-6）。"""
    from src.core.ops.pause import set_ops_paused

    class _Ops:
        def __init__(self) -> None:
            self.calls = 0

        async def active_comment(self, *_a, **_k):
            self.calls += 1

        async def follow(self, *_a, **_k):
            self.calls += 1

    ops = _Ops()
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, ops=ops)
    set_ops_paused(conn, True)

    asyncio.run(eng._job_daily_ops())

    assert ops.calls == 0
    assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 0


def test_daily_ops_binds_task_run_to_operation_log_and_reads_exact_outcome(cfg, conn) -> None:
    from src.core.task_context import current_task_run_id

    class _Ops:
        async def follow(self, platform, target):
            task_id = current_task_run_id()
            assert task_id is not None
            conn.execute(
                "INSERT INTO ops_logs (task_run_id, platform, action_type, target_author, status, executed_at) "
                "VALUES (?, ?, 'follow', ?, 'success', ?)",
                (task_id, platform.value, target, business_now(cfg).isoformat()),
            )
            conn.commit()

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, ops=_Ops())
    task_id = asyncio.run(eng._dispatch_action(Platform.XHS, "follow", "author-42"))

    assert task_id is not None
    log = conn.execute(
        "SELECT task_run_id, status FROM ops_logs WHERE target_author = 'author-42'"
    ).fetchone()
    task = conn.execute("SELECT status FROM task_runs WHERE id = ?", (task_id,)).fetchone()
    assert log["task_run_id"] == task_id
    assert log["status"] == "success"
    assert task["status"] == "success"


def test_tracked_scheduler_job_binds_parent_task_to_notifications(cfg, conn) -> None:
    from src.core.notify import Notifier
    from src.core.task_context import current_task_run_id

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={})

    async def fake_publish_polling():
        task_id = current_task_run_id()
        assert task_id is not None
        Notifier(cfg, conn)._save(
            "publish_confirmed", "info", "xhs", "tracked scheduler event", "test", None,
        )

    eng._job_publish_polling = fake_publish_polling
    asyncio.run(eng._run_tracked("_job_publish_polling"))

    task = conn.execute(
        "SELECT id, status FROM task_runs WHERE task_type = 'publish_polling' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    notification = conn.execute(
        "SELECT task_run_id FROM notifications WHERE title = 'tracked scheduler event'"
    ).fetchone()
    assert task is not None and task["status"] == "success"
    assert notification is not None and notification["task_run_id"] == task["id"]


def test_kb_scan_returns_failed_report_when_source_reader_raises(cfg, conn):
    class BrokenKb:
        def read(self):
            raise OSError("source unavailable")

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, kb_reader=BrokenKb())
    report = asyncio.run(eng._job_kb_scan())

    assert report["final_status"] == "failed"
    assert report["errors"] == ["选题库读取失败：OSError"]


def test_kb_sync_returns_failed_report_when_remote_source_fails(cfg, conn, monkeypatch):
    import src.core.kb_sources as kb_sources

    monkeypatch.setattr(kb_sources, "sync_all_remote", lambda _cfg: {"remote-a": "error"})
    eng = _engine(cfg, conn)
    report = asyncio.run(eng._job_kb_sync())

    assert report["final_status"] == "failed"
    assert report["remote_failed"] == 1
    assert any("remote-a" in item for item in report["errors"])
    assert report["scan"]["final_status"] == "success"


def test_kb_scan_surfaces_partial_source_errors_even_when_other_sources_succeed(cfg, conn):
    class PartiallyBrokenKb:
        def read(self):
            return (
                [TopicItem(id="good", title="可用选题", dimension="AI教育")],
                {"errors": 1},
            )

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, kb_reader=PartiallyBrokenKb())
    report = asyncio.run(eng._job_kb_scan())

    assert report["final_status"] == "failed"
    assert report["errors"] == ["有 1 个选题库来源读取失败"]
    assert conn.execute("SELECT COUNT(*) FROM topic_index WHERE id='good'").fetchone()[0] == 1


def test_comment_polling_reports_platform_fetch_failure(cfg, conn, seed, monkeypatch) -> None:
    """评论拉取异常不能只记日志后将全局队列任务伪装为成功。"""
    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at = ?, platform_note_url = ? WHERE id = ?",
        (datetime.now().isoformat(), "https://www.xiaohongshu.com/explore/test-note", draft_id),
    )
    conn.commit()

    class _Adapter:
        async def fetch_new_comments(self, _url):
            raise TimeoutError("simulated upstream timeout")

    class _Ops:
        async def passive_reply(self, *_args, **_kwargs):
            raise AssertionError("must not reply when comment fetch failed")

    monkeypatch.setattr("src.core.login_manager.has_login_state", lambda *_args, **_kwargs: True)
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={Platform.XHS: _Adapter()}, ops=_Ops())

    report = asyncio.run(eng._job_comment_polling())
    assert report["final_status"] == "failed"
    assert report["checked_drafts"] == 0
    assert report["replied"] == 0
    assert report["errors"] == ["xhs 评论读取失败（TimeoutError）"]



def test_daily_ops_reports_failed_action_to_global_queue(cfg, conn) -> None:
    """主动运营单条动作失败不能只留 task_run failed，却把调度队列标为成功。"""
    from types import SimpleNamespace
    from src.core.config import ActiveCommentTarget, OpsTargetsConfig, PlatformQuotaConfig

    quotas = {
        key: value.model_copy(update={"active_comments": 0, "follows": 0})
        for key, value in cfg.rhythm.daily_quotas.items()
    }
    quotas["xhs"] = PlatformQuotaConfig(active_comments=1, follows=0)
    rhythm_cfg = cfg.rhythm.model_copy(update={"daily_quotas": quotas})
    cfg_run = cfg.model_copy(update={
        "rhythm": rhythm_cfg,
        "ops_targets": {
            "xhs": OpsTargetsConfig(active_comments=[
                ActiveCommentTarget(
                    url="https://www.xiaohongshu.com/explore/test-target",
                    content="测试评论",
                ),
            ]),
        },
    })

    class _OpsRhythm:
        def is_within_window(self, *_args):
            return True

    class _Ops:
        rhythm = _OpsRhythm()

        async def active_comment(self, *_args):
            raise RuntimeError("simulated platform action failure")

    class _Guard:
        def check_comment(self, *_args, **_kwargs):
            return SimpleNamespace(passes=True, reason="")

    eng = SchedulerEngine(cfg_run, Path(cfg_run.paths.db_file))
    eng.wire(conn=conn, adapters={Platform.XHS: object()}, ops=_Ops(), guard=_Guard())

    report = asyncio.run(eng._job_daily_ops())
    assert report["final_status"] == "failed"
    assert report["dispatched"] == 1
    assert report["failed"] == 1
    assert "task_run_id=" in report["errors"][0]
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_type='daily_ops' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "failed"



def test_publish_polling_reports_metrics_fetch_failure(cfg, conn, seed, monkeypatch) -> None:
    """平台指标接口失败不能被误判为“用户尚未发布”。"""
    from src.adapters.base import FetchStatus, MetricsSnapshot

    round_id = seed.round()
    seed.draft(
        round_id, platform="xhs", draft_status="draft_saved",
        fetch_status=None, saved_at=datetime.now().isoformat(),
    )

    class _Adapter:
        async def fetch_metrics(self, *_args, **_kwargs):
            return MetricsSnapshot(
                platform=Platform.XHS,
                fetch_status=FetchStatus.FAILED,
                error_msg="simulated upstream timeout",
            )

    monkeypatch.setattr("src.core.login_manager.has_login_state", lambda *_args, **_kwargs: True)
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={Platform.XHS: _Adapter()})

    report = asyncio.run(eng._job_publish_polling())
    assert report["final_status"] == "failed"
    assert report["scanned"] == 1
    assert report["confirmed"] == 0
    assert report["errors"] == ["xhs 发布状态读取未完成（failed）"]



def test_hot_topic_refresh_waits_for_work_and_reports_partial_failures(cfg, conn, monkeypatch):
    import src.core.hot_topic_service as hot_topic_service

    monkeypatch.setattr(hot_topic_service, "run_once", lambda *_args, **_kwargs: {
        "running": False,
        "error": None,
        "report": {"source-a": "collected", "source-b": "failed"},
        "match": {"status": "partial", "failed_batches": 1},
    })
    eng = _engine(cfg, conn)
    report = asyncio.run(eng._job_hot_topics_refresh())

    assert report["final_status"] == "failed"
    assert report["errors"] == [
        "有 1 个热点来源采集失败",
        "热点标签匹配未完整完成",
    ]


def test_hot_topic_scheduler_job_is_submitted_to_global_fifo_queue(cfg, conn):
    from src.core.scheduler import _invoke

    class Queue:
        def __init__(self):
            self.items = []

        def enqueue(self, **kwargs):
            self.items.append(kwargs)

    queue = Queue()
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, task_queue=queue, tenant_id="tenant-hot")
    asyncio.run(_invoke(eng, "_job_hot_topics_refresh"))

    assert len(queue.items) == 1
    task = queue.items[0]
    assert task["task_type"] == "scheduler_job"
    assert task["tenant_id"] == "tenant-hot"
    assert task["payload"]["method"] == "_job_hot_topics_refresh"
    assert task["payload"]["execution_snapshot"]["schema_version"] == 1


def test_round_review_reports_analysis_failure_while_finishing_state_transition(cfg, conn, seed):
    round_id = seed.round(status="analyzing")

    class RoundMachine:
        def decide_analyzing_exit(self, _round_id):
            return "completed"

        def transition(self, current_id, target, *, reason):
            assert current_id == round_id
            assert target == "completed"
            return True

    class BrokenSpiral:
        def analyze_round(self, _round_id):
            raise RuntimeError("sensitive detail must not enter queue error")

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, sm_round=RoundMachine(), spiral=BrokenSpiral())
    report = asyncio.run(eng._job_round_review())

    assert report["final_status"] == "failed"
    assert report["rounds_analyzed"] == 1
    assert report["errors"] == [f"轮次 {round_id} 螺旋复盘失败（RuntimeError）"]


def test_data_retention_reports_partial_hot_topic_cleanup_failure(cfg, conn, monkeypatch):
    import src.core.hot_topic_dedup as hot_topic_dedup

    def broken_prune(*_args, **_kwargs):
        raise RuntimeError("sensitive filesystem detail")

    monkeypatch.setattr(hot_topic_dedup, "prune", broken_prune)
    eng = _engine(cfg, conn)
    report = asyncio.run(eng._job_data_retention_cleanup())

    assert report["final_status"] == "failed"
    assert report["errors"] == ["热点保留清理失败（RuntimeError）"]
    assert "deleted_screenshots" in report
    assert "cleared_raw_responses" in report


def test_round_timeout_sweeper_reports_missing_runtime_dependencies(cfg):
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    report = asyncio.run(eng._job_round_timeout_sweeper())
    assert report["final_status"] == "failed"
    assert report["errors"] == ["轮次超时检查依赖未注入"]


def test_round_timeout_sweeper_reports_rounds_without_phase_start(cfg, conn, seed):
    round_id = seed.round(status="producing")
    conn.execute(
        "UPDATE spiral_rounds SET producing_started_at=NULL, transition_log='[]' WHERE id=?",
        (round_id,),
    )
    conn.commit()

    class DraftMachine:
        def check_expired_drafts(self, **_kwargs):
            return []

    class RoundMachine:
        def transition(self, *_args, **_kwargs):
            raise AssertionError("a round without a phase start must not transition")

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, sm_round=RoundMachine(), sm_draft=DraftMachine())
    report = asyncio.run(eng._job_round_timeout_sweeper())

    assert report["final_status"] == "failed"
    assert report["rounds_checked"] == 1
    assert report["errors"] == [f"轮次 {round_id} 在 producing 缺少阶段起点"]


def test_publish_polling_treats_unmatched_work_as_expected_pending(cfg, conn, seed, monkeypatch):
    from src.adapters.base import FetchStatus, MetricsSnapshot

    round_id = seed.round()
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved",
        fetch_status=None, saved_at=datetime.now().isoformat(),
    )

    class _Adapter:
        async def fetch_metrics(self, *_args, **_kwargs):
            return MetricsSnapshot(
                platform=Platform.XHS,
                fetch_status=FetchStatus.PENDING,
                error_msg="作品列表暂未匹配到该草稿",
            )

    monkeypatch.setattr("src.core.login_manager.has_login_state", lambda *_args, **_kwargs: True)
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={Platform.XHS: _Adapter()})

    report = asyncio.run(eng._job_publish_polling())
    assert report["final_status"] == "success"
    assert report["scanned"] == 1
    assert report["confirmed"] == 0
    assert report["pending"] == 1
    assert report["errors"] == []
    row = conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()
    assert row["status"] == "draft_saved"


def test_scheduler_jobs_distinguish_missing_dependencies_from_intentional_skips(cfg):
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))

    data_fetch = asyncio.run(eng._job_data_fetch("missing-draft"))
    daily_ops = asyncio.run(eng._job_daily_ops())
    comment_polling = asyncio.run(eng._job_comment_polling())
    cb_recovery = asyncio.run(eng._job_cb_recovery())
    autogrow = asyncio.run(eng._job_autogrow_next_round())

    assert data_fetch["final_status"] == "failed"
    assert daily_ops["final_status"] == "failed"
    assert comment_polling["final_status"] == "failed"
    assert cb_recovery["final_status"] == "failed"
    assert autogrow["final_status"] == "skipped"


def test_publish_confirmation_atomic_transition_schedules_fetch_once(cfg, conn, seed, monkeypatch):
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(round_id, draft_status="draft_saved", fetch_status=None)
    engine = _engine(cfg, conn)
    engine.wire(conn=conn, adapters={}, sm_draft=DraftStateMachine(conn))
    scheduled = []
    monkeypatch.setattr(engine, "schedule_data_fetch", lambda draft_id, run_at, attempt=1: scheduled.append((draft_id, attempt)))

    assert engine.on_user_published(draft_id, via="first-confirm") is True
    assert engine.on_user_published(draft_id, via="duplicate-confirm") is False
    assert scheduled == [(draft_id, 1)]
    row = conn.execute(
        "SELECT status, user_confirmed_at FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()
    assert row["status"] == "user_published"
    assert row["user_confirmed_at"]


def test_tracked_scheduler_task_stores_safe_exception_message(cfg, conn, monkeypatch):
    eng = _engine(cfg, conn)

    async def fail_with_private_detail():
        raise RuntimeError("https://internal.example/private?token=secret")

    monkeypatch.setattr(eng, "_job_kb_scan", fail_with_private_detail)
    try:
        asyncio.run(eng._run_tracked("_job_kb_scan"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected scheduler job failure")

    row = conn.execute(
        "SELECT status, error_msg FROM task_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error_msg"] == "定时任务执行失败，请检查服务端运行日志"
    assert "internal.example" not in row["error_msg"]
    assert "secret" not in row["error_msg"]



def test_publish_confirmation_rolls_back_status_when_note_url_write_fails(cfg, conn, seed):
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(round_id, draft_status="draft_saved", fetch_status=None)
    conn.execute("""
        CREATE TRIGGER reject_publish_note_url
        BEFORE UPDATE OF platform_note_url ON drafts
        WHEN NEW.platform_note_url IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'injected note url failure'); END
    """)
    conn.commit()

    engine = _engine(cfg, conn)
    engine.wire(conn=conn, adapters={}, sm_draft=DraftStateMachine(conn))
    scheduled = []
    engine.schedule_data_fetch = lambda *args, **kwargs: scheduled.append(args)

    try:
        engine.on_user_published(
            draft_id, via="platform-polling",
            note_url="https://platform.example/note/123",
        )
    except sqlite3.IntegrityError as exc:
        assert "injected note url failure" in str(exc)
    else:
        raise AssertionError("expected note URL write failure")

    row = conn.execute(
        "SELECT status, user_confirmed_at, platform_note_url FROM drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
    assert row["status"] == "draft_saved"
    assert row["user_confirmed_at"] is None
    assert row["platform_note_url"] is None
    assert scheduled == []



def test_kb_scan_rolls_back_all_topic_inserts_when_one_insert_fails(conn: sqlite3.Connection, cfg) -> None:
    eng = _engine(cfg, conn)
    conn.execute("""
        CREATE TRIGGER reject_second_kb_topic
        BEFORE INSERT ON topic_index
        WHEN NEW.id = 'kt2'
        BEGIN SELECT RAISE(ABORT, 'injected topic insert failure'); END
    """)
    conn.commit()

    try:
        asyncio.run(eng._job_kb_scan())
    except sqlite3.IntegrityError as exc:
        assert "injected topic insert failure" in str(exc)
    else:
        raise AssertionError("expected topic insert failure")

    assert conn.execute("SELECT COUNT(*) FROM topic_index").fetchone()[0] == 0



def test_tracked_scheduler_job_rolls_back_implicit_write_on_exception(cfg, conn):
    import threading
    import pytest
    from src.core.db import LockedConnection

    locked = LockedConnection(conn)
    locked.execute("CREATE TABLE IF NOT EXISTS scheduler_txn_probe (value INTEGER)")
    locked.commit()
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=locked, adapters={})

    async def abandon_transaction():
        locked.execute("INSERT INTO scheduler_txn_probe VALUES (1)")
        raise RuntimeError("simulated scheduler failure")

    eng._job_abandon_transaction = abandon_transaction
    with pytest.raises(RuntimeError, match="simulated scheduler failure"):
        asyncio.run(eng._run_tracked("_job_abandon_transaction"))

    assert locked.execute("SELECT COUNT(*) FROM scheduler_txn_probe").fetchone()[0] == 0
    completed = threading.Event()
    reader = threading.Thread(
        target=lambda: (locked.execute("SELECT COUNT(*) FROM scheduler_txn_probe").fetchone(), completed.set())
    )
    reader.start()
    reader.join(timeout=1)
    assert completed.is_set(), "scheduler finalizer leaked the retained transaction lock"


def _verified_review_task(conn, seed, actor="session:review-creator"):
    from src.core.operating_feedback import create_publish_verification

    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="user_published", fetch_status=None)
    published_at = "2026-11-04T02:00:00+00:00"
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=?,published_at=? WHERE id=?",
        (published_at, published_at, draft_id),
    )
    result = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id=f"note-{draft_id}",
        content_url=f"https://www.xiaohongshu.com/explore/{draft_id}",
        published_at=published_at, actor=actor,
    )
    conn.commit()
    return draft_id, result["review_task_id"]


def test_independent_review_collection_records_platform_facts_and_retries_partial(cfg, conn, seed):
    from src.adapters.base import FetchStatus, MetricsSnapshot

    draft_id, task_id = _verified_review_task(conn, seed)

    class _Adapter:
        async def fetch_metrics(self, *_args, **_kwargs):
            return MetricsSnapshot(
                platform=Platform.XHS, fetch_status=FetchStatus.PARTIAL,
                views=0, likes=8, comments=None,
            )

    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={Platform.XHS: _Adapter()}, tenant_id="tenant-test")
    from src.core.task_context import bind_task_identity
    with bind_task_identity(
        initiating_actor="session:review-creator",
        executing_actor="service:global-task-worker",
        tenant_id="tenant-test",
        task_id="queue-review-run-1",
    ):
        report = asyncio.run(eng._job_review_collection(task_id, attempt=1))

    assert report["final_status"] == "partial"
    run_actor = conn.execute(
        "SELECT recorded_by, recorded_executing_actor, recorded_queue_task_id FROM review_collection_runs WHERE task_id=?", (task_id,)
    ).fetchone()
    assert run_actor["recorded_by"] == "session:review-creator"
    assert run_actor["recorded_executing_actor"] == "service:global-task-worker"
    assert run_actor["recorded_queue_task_id"] == "queue-review-run-1"
    assert report["retry_scheduled"] is True
    assert f"review_collect_{task_id}_initial_a2" in {job.id for job in eng.scheduler.get_jobs()}
    assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == "user_published"
    task = conn.execute("SELECT status,attempt_count FROM review_tasks WHERE task_id=?", (task_id,)).fetchone()
    assert task["status"] == "scheduled"
    assert task["attempt_count"] == 1
    facts = conn.execute(
        "SELECT metric_key,value,collection_status FROM metric_facts "
        "WHERE source_record_type='review_collection_run' ORDER BY metric_key"
    ).fetchall()
    assert [(row["metric_key"], row["value"]) for row in facts] == [("likes", 8.0), ("views", 0.0)]
    assert all(row["collection_status"] == "partial" for row in facts)


def test_independent_review_collection_failure_is_recorded_and_never_changes_draft(cfg, conn, seed):
    draft_id, task_id = _verified_review_task(conn, seed)
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, tenant_id="tenant-test")

    report = asyncio.run(eng._job_review_collection(task_id, attempt=1))

    assert report["final_status"] == "failed"
    assert report["retry_scheduled"] is True
    assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == "user_published"
    run = conn.execute(
        "SELECT status,error_message,metrics_json FROM review_collection_runs WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert run["status"] == "failed"
    assert "适配器未启用" in run["error_message"]
    assert run["metrics_json"] == "{}"
    assert conn.execute(
        "SELECT COUNT(*) FROM metric_facts WHERE source_record_type='review_collection_run'"
    ).fetchone()[0] == 0


def test_scheduler_restores_persisted_review_tasks_after_restart(cfg, conn, seed):
    draft_id, task_id = _verified_review_task(conn, seed)
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={}, tenant_id="tenant-test")

    eng._bind_persisted_review_tasks()

    jobs = {job.id: job for job in eng.scheduler.get_jobs()}
    assert f"review_collect_{task_id}_initial_a1" in jobs
    assert jobs[f"review_collect_{task_id}_initial_a1"].kwargs["task_id"] == task_id
    assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == "user_published"


def test_stale_review_collection_job_is_ignored_after_manual_retry(cfg, conn, seed):
    from src.core.operating_feedback import create_publish_verification, retry_review_task

    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=?,published_at=? WHERE id=?",
        ("2026-11-04T02:00:00+00:00", "2026-11-04T02:00:00+00:00", draft_id),
    )
    conn.commit()
    verification = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id="note-stale-cycle",
        content_url="https://www.xiaohongshu.com/explore/stale-cycle",
        published_at="2026-11-04T02:00:00+00:00",
    )
    task_id = verification["review_task_id"]
    conn.execute("UPDATE review_tasks SET status='partial' WHERE task_id=?", (task_id,))
    conn.commit()
    retry_review_task(conn, task_id)
    engine = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    engine.wire(conn=conn, adapters={}, tenant_id="tenant-test")

    result = asyncio.run(engine._job_review_collection(task_id, attempt=1, cycle_id="initial"))

    assert result["final_status"] == "skipped"
    assert "周期已更新" in result["reason"]


def test_review_collection_queue_preserves_initiating_actor(cfg, conn, seed):
    from datetime import datetime, timezone

    draft_id, task_id = _verified_review_task(conn, seed, actor="session:review-initiator")

    class _Queue:
        def __init__(self):
            self.items = []

        def enqueue(self, **kwargs):
            self.items.append(kwargs)

    queue = _Queue()
    engine = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    engine.wire(conn=conn, adapters={}, task_queue=queue, tenant_id="tenant-test")
    engine.schedule_review_collection(task_id, datetime(2026, 11, 5, 10, tzinfo=timezone.utc))

    assert len(queue.items) == 1
    assert queue.items[0]["payload"]["initiating_actor"] == "session:review-initiator"
    assert queue.items[0]["payload"]["args"] == [task_id, 1, "initial"]
    assert conn.execute(
        "SELECT created_by FROM review_tasks WHERE task_id=?", (task_id,)
    ).fetchone()["created_by"] == "session:review-initiator"


def test_publish_polling_fingerprint_includes_every_image():
    import json
    from src.core.scheduler import _draft_payload_content_hash

    tags = json.dumps(["tag"])
    first = json.dumps(["/assets/cover.png", "/assets/page-1.png"])
    changed_non_cover = json.dumps(["/assets/cover.png", "/assets/page-2.png"])
    reordered = json.dumps(["/assets/page-1.png", "/assets/cover.png"])

    baseline = _draft_payload_content_hash("title", "body", tags, first)
    assert baseline != _draft_payload_content_hash("title", "body", tags, changed_non_cover)
    assert baseline != _draft_payload_content_hash("title", "body", tags, reordered)
    assert baseline == _draft_payload_content_hash(
        "title", "body", tags, json.dumps(["/assets/cover.png", "/assets/page-1.png"]),
    )


def test_data_fetch_uses_saved_platform_title_after_local_edit(cfg, conn, seed) -> None:
    """采集匹配必须使用平台存稿时的标题快照，不能被后续本地改名破坏。"""
    from src.adapters.base import FetchStatus, MetricsSnapshot
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET saved_platform_title = ? WHERE id = ?",
        ("平台实际存稿标题", draft_id),
    )
    conn.execute(
        "UPDATE contents SET title = ? WHERE id = (SELECT content_id FROM drafts WHERE id = ?)",
        ("本地后来修改的标题", draft_id),
    )
    conn.commit()

    class _Ad:
        seen_title = None

        async def fetch_metrics(self, _platform_draft_id, title):
            self.seen_title = title
            return MetricsSnapshot(
                platform=Platform.XHS, fetch_status=FetchStatus.FAILED, error_msg="测试未执行平台读回",
            )

    adapter = _Ad()
    eng = SchedulerEngine(cfg, Path(cfg.paths.db_file))
    eng.wire(conn=conn, adapters={Platform.XHS: adapter}, sm_draft=DraftStateMachine(conn))
    asyncio.run(eng._job_data_fetch(draft_id, attempt=1))

    assert adapter.seen_title == "平台实际存稿标题"


def test_wechat_publish_confirmation_waits_for_manual_metrics(cfg, conn, seed, monkeypatch):
    """视频号无逐篇读回时，发布确认不得排入必然失败的数据采集任务。"""
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(round_id, draft_status="draft_saved", fetch_status=None)
    conn.execute("UPDATE drafts SET platform='wechat_channels' WHERE id=?", (draft_id,))
    conn.commit()
    engine = _engine(cfg, conn)
    engine.wire(conn=conn, adapters={}, sm_draft=DraftStateMachine(conn))
    scheduled = []
    monkeypatch.setattr(
        engine, "schedule_data_fetch",
        lambda *args, **kwargs: scheduled.append((args, kwargs)),
    )

    assert engine.on_user_published(draft_id, via="manual-confirm") is True
    row = conn.execute(
        "SELECT status,user_confirmed_at,error_msg FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()
    assert row["status"] == "user_published"
    assert row["user_confirmed_at"]
    assert "手工回填" in row["error_msg"]
    assert scheduled == []


def test_wechat_backfill_does_not_enqueue_unsupported_readback(cfg, conn, seed):
    """启动补登记不能把视频号的人工回填状态重新排成自动采集。"""
    from datetime import timedelta

    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    conn.execute(
        "UPDATE drafts SET platform='wechat_channels',user_confirmed_at=? WHERE id=?",
        (old, draft_id),
    )
    conn.commit()
    engine = _engine(cfg, conn)

    assert engine.backfill_data_fetch() == 0
    assert not any(job.id.startswith(f"data_fetch_{draft_id}_") for job in engine.scheduler.get_jobs())


def test_legacy_wechat_data_fetch_task_is_safely_skipped(cfg, conn, seed):
    """旧版本遗留的队列任务不得把视频号无读回能力误判为 failed。"""
    from src.core.state_machine import DraftStateMachine

    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(round_id, draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET platform='wechat_channels',user_confirmed_at=? WHERE id=?",
        (datetime.now().isoformat(), draft_id),
    )
    conn.commit()
    engine = _engine(cfg, conn)
    engine.wire(conn=conn, adapters={}, sm_draft=DraftStateMachine(conn))

    report = asyncio.run(engine._job_data_fetch(draft_id, attempt=1))
    row = conn.execute(
        "SELECT status,error_msg FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()
    assert report == {
        "final_status": "success", "skipped": "manual_metrics_required",
        "errors": [], "attempt": 1,
    }
    assert row["status"] == "user_published"
    assert "手工回填" in row["error_msg"]
    assert conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE draft_id=?", (draft_id,),
    ).fetchone()[0] == 0
    assert not any(job.id.startswith(f"data_fetch_{draft_id}_") for job in engine.scheduler.get_jobs())
