"""生产线恢复路径回归测试：干跑后可继续存草稿，复盘异常不会遗留 running 任务。"""

from __future__ import annotations

import asyncio
import json

from src.adapters.base import DraftResult, Platform
from src.core.guard.ultimate_goal_checker import CheckOutcome, CheckResult
from src.core.orchestration import default_steps, save_steps
from src.core.round_executor import RoundExecutor
from src.core.recovery import RecoveryManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine


class _SuccessfulDraftAdapter:
    def __init__(self):
        self.payloads = []

    async def save_draft(self, payload):
        self.payloads.append(payload)
        return DraftResult(success=True, platform_draft_id="platform-draft-123")


class _PassingGuard:
    def check_content(self, title, body, tags, *, platform):
        return CheckOutcome(result=CheckResult.PASS, reason="测试通过")


class _RejectingGuard:
    def check_content(self, title, body, tags, *, platform):
        return CheckOutcome(result=CheckResult.FAIL, reason="测试命中红线")


def _executor(cfg, conn, adapters=None, guard=None):
    return RoundExecutor(
        cfg=cfg,
        conn=conn,
        scoring=None,
        guard=guard if guard is not None else _PassingGuard(),
        adapters=adapters or {},
        sm_round=RoundStateMachine(conn),
        sm_draft=DraftStateMachine(conn),
    )


def test_empty_topic_pool_is_blocked_before_round_enters_producing(cfg, conn, seed, monkeypatch):
    monkeypatch.setattr("src.core.round_executor.load_pool", lambda *args, **kwargs: ([], {"kb_error": None}))
    round_id = seed.round(status="created")
    executor = _executor(cfg, conn)
    try:
        asyncio.run(executor.run(round_id))
    except ValueError as exc:
        assert "运行前检查未通过" in str(exc)
        assert "选题" in str(exc)
    else:
        raise AssertionError("empty topic pool must be blocked before execution")

    status = conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0]
    assert status == "created"


def test_failed_round_with_existing_content_cannot_be_retried(cfg, conn, seed):
    round_id = seed.round(status="failed")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    executor = _executor(cfg, conn)
    try:
        asyncio.run(executor.run(round_id))
    except ValueError as exc:
        assert "已产生内容或草稿" in str(exc)
    else:
        raise AssertionError("failed round with output must not be rerun")

    status = conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0]
    assert status == "failed"


def test_dry_run_pending_drafts_can_be_saved_without_regenerating(cfg, conn, seed):
    round_id = seed.round(status="producing")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    conn.execute(
        "UPDATE contents SET title = ?, body = ?, tags = ?, image_paths = ? "
        "WHERE id = (SELECT content_id FROM drafts WHERE id = ?)",
        ("恢复测试标题", "恢复测试正文", json.dumps(["AI", "运营"]), json.dumps(["/tmp/card.png"]), draft_id),
    )
    conn.execute(
        "UPDATE spiral_rounds SET run_report = ? WHERE id = ?",
        (json.dumps({
            "round_id": round_id,
            "round_number": 1,
            "contents_created": 1,
            "drafts_created": 1,
            "drafts_saved": 0,
            "drafts_failed": 0,
            "errors": [],
            "steps": [
                {"step": "produce", "contents": 1},
                {"step": "dry_run", "note": "本次未执行存草稿"},
            ],
            "final_status": "producing",
        }), round_id),
    )
    conn.commit()

    adapter = _SuccessfulDraftAdapter()
    executor = _executor(cfg, conn, {Platform.XHS: adapter})
    # 真实队列 Worker 会按任务 tenant_id 绑定登录态目录；测试也显式模拟该上下文。
    from src.core import login_manager
    with login_manager.tenant_login_dir(cfg.paths.data_dir):
        state_path = login_manager._platform_state_path(Platform.XHS)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{}", encoding="utf-8")
        result = asyncio.run(executor.save_pending_drafts(round_id))

    assert result["drafts_attempted"] == 1
    assert result["drafts_saved"] == 1
    assert result["drafts_failed"] == 0
    assert result["pending_drafts"] == 0
    assert result["final_status"] == "awaiting_publish"
    assert len(adapter.payloads) == 1
    assert adapter.payloads[0].title == "恢复测试标题"
    assert adapter.payloads[0].body == "恢复测试正文"
    assert adapter.payloads[0].tags == ["AI", "运营"]
    assert adapter.payloads[0].image_paths == ["/tmp/card.png"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"
    from src.core.content_version import content_version_hash
    saved_hash = conn.execute(
        "SELECT saved_content_hash FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    assert saved_hash == content_version_hash(
        adapter.payloads[0].title,
        adapter.payloads[0].body,
        adapter.payloads[0].tags,
        adapter.payloads[0].image_paths,
    )
    saved_title = conn.execute(
        "SELECT saved_platform_title FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    assert saved_title == adapter.payloads[0].title
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "awaiting_publish"

    stored = json.loads(conn.execute(
        "SELECT run_report FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0])
    assert not any(step.get("step") == "dry_run" for step in stored["steps"])
    assert stored["resume_history"][-1]["drafts_saved"] == 1
    assert stored["step_status"]["save_draft"]["status"] == "success"


def test_resume_rechecks_current_content_before_any_platform_save(cfg, conn, seed):
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending",
        fetch_status=None, topic_id="edited-invalid-content",
    )
    conn.execute(
        "UPDATE contents SET title = ?, body = ?, tags = ?, guard_outcome = NULL, guard_reason = NULL "
        "WHERE id = (SELECT content_id FROM drafts WHERE id = ?)",
        ("编辑后的违规标题", "当前正文", json.dumps(["测试"]), draft_id),
    )
    conn.commit()

    adapter = _SuccessfulDraftAdapter()
    executor = _executor(cfg, conn, {Platform.XHS: adapter}, guard=_RejectingGuard())
    try:
        asyncio.run(executor.save_pending_drafts(round_id))
    except ValueError as exc:
        assert "未通过当前质量门" in str(exc)
        assert "测试命中红线" in str(exc)
    else:
        raise AssertionError("当前内容未通过质量门，不应执行存草稿")

    # 即使质检拦截，也先持久化当前文案的失败结果，方便 UI 展示与修订。
    content = conn.execute(
        "SELECT guard_outcome, guard_reason FROM contents WHERE id = "
        "(SELECT content_id FROM drafts WHERE id = ?)", (draft_id,),
    ).fetchone()
    assert content["guard_outcome"] == "fail"
    assert "测试命中红线" in content["guard_reason"]
    assert adapter.payloads == []  # 质检前不能触发任何平台副作用
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_pending"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "producing"


def test_resume_merges_draft_ids_instead_of_losing_prior_successes(cfg, conn, seed, monkeypatch):
    monkeypatch.setattr("src.core.login_manager.has_login_state", lambda _platform: True)
    round_id = seed.round(status="producing")
    previous_saved_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved",
        fetch_status=None, topic_id="already-saved",
    )
    pending_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending",
        fetch_status=None, topic_id="still-pending",
    )
    conn.execute(
        "UPDATE spiral_rounds SET draft_ids = ?, run_report = ? WHERE id = ?",
        (
            json.dumps([previous_saved_id]),
            json.dumps({
                "round_id": round_id, "round_number": 1,
                "contents_created": 2, "drafts_created": 2,
                "drafts_saved": 1, "drafts_failed": 0, "errors": [],
                "steps": [{"step": "produce", "contents": 2},
                          {"step": "save_drafts", "saved": 1, "failed": 0}],
                "final_status": "producing",
            }),
            round_id,
        ),
    )
    conn.commit()

    adapter = _SuccessfulDraftAdapter()
    result = asyncio.run(_executor(cfg, conn, {Platform.XHS: adapter}).save_pending_drafts(round_id))
    saved_ids = json.loads(conn.execute(
        "SELECT draft_ids FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0])

    assert result["drafts_saved"] == 1
    assert result["final_status"] == "awaiting_publish"
    assert previous_saved_id in saved_ids
    assert pending_id in saved_ids
    assert len(saved_ids) == len(set(saved_ids))


def test_resume_never_saves_manually_rejected_content(cfg, conn, seed, monkeypatch):
    monkeypatch.setattr("src.core.login_manager.has_login_state", lambda _platform: True)
    round_id = seed.round(status="producing")
    rejected_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending",
        fetch_status=None, topic_id="rejected-topic",
    )
    accepted_id = seed.draft(
        round_id, platform="douyin", draft_status="draft_pending",
        fetch_status=None, topic_id="approved-topic",
    )
    rejected_content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (rejected_id,)
    ).fetchone()[0]
    accepted_content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (accepted_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE contents SET review_rejected = 1, review_rejection_reason = ? WHERE id = ?",
        ("观点不符合定位", rejected_content_id),
    )
    conn.execute(
        "UPDATE contents SET title = ?, body = ?, tags = ? WHERE id = ?",
        ("审核通过标题", "审核通过正文", json.dumps(["test"]), accepted_content_id),
    )
    conn.commit()

    adapter = _SuccessfulDraftAdapter()
    result = asyncio.run(
        _executor(cfg, conn, {
            Platform.XHS: adapter,
            Platform.DOUYIN: adapter,
        }).save_pending_drafts(round_id)
    )

    assert result["drafts_attempted"] == 1
    assert result["drafts_saved"] == 1
    assert result["pending_drafts"] == 1
    assert result["final_status"] == "producing"
    assert [payload.title for payload in adapter.payloads] == ["审核通过标题"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (rejected_id,)
    ).fetchone()[0] == "draft_pending"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (accepted_id,)
    ).fetchone()[0] == "draft_saved"


def test_unlogged_platform_skips_save_and_keeps_draft_pending(cfg, conn, seed, monkeypatch):
    round_id = seed.round(status="producing")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    adapter = _SuccessfulDraftAdapter()
    monkeypatch.setattr("src.core.login_manager.has_login_state", lambda _platform: False)
    executor = _executor(cfg, conn, {Platform.XHS: adapter})
    report = {"steps": [], "drafts_saved": 0, "drafts_failed": 0}

    asyncio.run(executor._save_all_drafts([{
        "draft_id": draft_id,
        "content_id": conn.execute("SELECT content_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0],
        "platform": Platform.XHS,
        "title": "待存内容",
        "body": "正文",
        "tags": [],
        "image_paths": [],
    }], round_id, report))

    assert adapter.payloads == []
    assert report["drafts_skipped_login"] == 1
    assert report["steps"][0]["step"] == "save_drafts_skipped_login"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_pending"


def test_restart_keeps_dry_run_pending_drafts_recoverable(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    conn.execute(
        "UPDATE spiral_rounds SET run_report = ? WHERE id = ?",
        (json.dumps({"steps": [{"step": "produce"}, {"step": "dry_run"}]}), round_id),
    )
    conn.commit()

    recovered = RecoveryManager(cfg, conn)._stale_producing_rounds()

    assert round_id not in recovered
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "producing"


def test_restart_still_marks_interrupted_non_dry_run_without_saved_drafts_failed(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    conn.execute(
        "UPDATE spiral_rounds SET run_report = ? WHERE id = ?",
        (json.dumps({"steps": [{"step": "produce"}]}), round_id),
    )
    conn.commit()

    RecoveryManager(cfg, conn)._stale_producing_rounds()

    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "failed"


def test_resume_requires_enabled_save_draft_step(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    steps = default_steps()
    for step in steps:
        if step["id"] in ("save_draft", "await_publish"):
            step["enabled"] = False
    save_steps(conn, steps)

    executor = _executor(cfg, conn, {Platform.XHS: _SuccessfulDraftAdapter()})
    try:
        asyncio.run(executor.save_pending_drafts(round_id))
    except ValueError as exc:
        assert "已关闭" in str(exc)
    else:
        raise AssertionError("关闭存草稿编排时不应执行平台存草稿")
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "producing"
    assert conn.execute(
        "SELECT status FROM drafts WHERE content_id IN "
        "(SELECT id FROM contents WHERE round_id = ?)", (round_id,)
    ).fetchone()[0] == "draft_pending"


def test_empty_topic_selection_persists_failed_run_report(cfg, conn, seed, monkeypatch):
    round_id = seed.round(status="created")
    executor = _executor(cfg, conn)
    executor._resolve_platforms = lambda values: [Platform.XHS]
    executor._select_topics = lambda topic_count, report: []
    monkeypatch.setattr("src.core.round_executor.load_pool", lambda *args, **kwargs: ([{"id": "topic-1"}], {}))
    monkeypatch.setattr(
        "src.core.plan_version.PlanVersionManager.load_plan",
        lambda self: ({"topic_count_per_platform": 1, "enabled_platforms": ["xhs"]}, "sig", None, "test"),
    )
    monkeypatch.setattr(executor, "_resolve_feedback", lambda *_args, **_kwargs: None)

    report = asyncio.run(executor.run(round_id))
    assert report["final_status"] == "failed"
    assert report["finished_at"]
    stored_raw = conn.execute(
        "SELECT run_report FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0]
    assert stored_raw
    stored = json.loads(stored_raw)
    assert stored["final_status"] == "failed"
    assert "选题库无可用选题" in stored["errors"]
    task = conn.execute(
        "SELECT status FROM task_runs WHERE task_type = 'round_pipeline' AND biz_id = ? "
        "ORDER BY id DESC LIMIT 1", (str(round_id),)
    ).fetchone()
    assert task is not None and task["status"] == "failed"


def test_feedback_resolution_exception_marks_round_and_task_failed(cfg, conn, seed, monkeypatch):
    round_id = seed.round(status="created")
    executor = _executor(cfg, conn)
    executor._resolve_platforms = lambda values: [Platform.XHS]
    monkeypatch.setattr("src.core.round_executor.load_pool", lambda *args, **kwargs: ([{"id": "topic-1"}], {}))
    monkeypatch.setattr(
        "src.core.plan_version.PlanVersionManager.load_plan",
        lambda self: ({"topic_count_per_platform": 1, "enabled_platforms": ["xhs"]}, "sig", None, "test"),
    )
    monkeypatch.setattr(
        executor, "_resolve_feedback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("feedback parsing failed")),
    )

    report = asyncio.run(executor.run(round_id))
    assert report["final_status"] == "failed"
    assert report["failure_code"] == "pipeline_error"
    assert any("生产流程失败" in msg for msg in report["errors"])
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "failed"
    task = conn.execute(
        "SELECT status FROM task_runs WHERE task_type = 'round_pipeline' AND biz_id = ? "
        "ORDER BY id DESC LIMIT 1", (str(round_id),)
    ).fetchone()
    assert task is not None and task["status"] == "failed"


def _finalization_report():
    return {"errors": [], "steps": []}


def test_finalize_marks_all_cancelled_drafts_as_cancelled_round(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="cancelled", fetch_status=None)
    seed.draft(round_id, platform="douyin", draft_status="cancelled", fetch_status=None, topic_id="t2")

    report = _finalization_report()
    _executor(cfg, conn)._finalize_round(round_id, report)

    assert report["final_status"] == "cancelled"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "cancelled"


def test_finalize_marks_all_expired_drafts_as_cancelled_round(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="expired", fetch_status=None)
    seed.draft(round_id, platform="douyin", draft_status="expired", fetch_status=None, topic_id="t2")

    report = _finalization_report()
    _executor(cfg, conn)._finalize_round(round_id, report)

    assert report["final_status"] == "cancelled"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "cancelled"


def test_finalize_failed_and_cancelled_without_saved_drafts_fails_round(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="failed", fetch_status=None)
    seed.draft(round_id, platform="douyin", draft_status="cancelled", fetch_status=None, topic_id="t2")

    report = _finalization_report()
    _executor(cfg, conn)._finalize_round(round_id, report)

    assert report["final_status"] == "failed"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "failed"


def test_finalize_saved_draft_with_cancelled_sibling_still_awaits_publish(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="draft_saved", fetch_status=None)
    seed.draft(round_id, platform="douyin", draft_status="cancelled", fetch_status=None, topic_id="t2")

    report = _finalization_report()
    _executor(cfg, conn)._finalize_round(round_id, report)

    assert report["final_status"] == "awaiting_publish"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "awaiting_publish"




def test_unexpected_draft_adapter_exception_requires_human_verification(
    cfg, conn, seed, monkeypatch,
):
    from src.core import login_manager

    monkeypatch.setattr(login_manager, "has_login_state", lambda _platform: True)
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )

    class BrokenAdapter:
        def __init__(self):
            self.calls = 0

        async def save_draft(self, payload):
            self.calls += 1
            raise RuntimeError("https://internal.example/private?token=secret")

    adapter = BrokenAdapter()
    executor = _executor(cfg, conn, {Platform.XHS: adapter})
    report = {"drafts_failed": 0, "drafts_saved": 0, "steps": []}
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0]
    rows = [{
        "content_id": content_id, "draft_id": draft_id, "platform": Platform.XHS,
        "title": "测试标题", "body": "测试正文", "tags": [], "image_paths": [],
    }]
    asyncio.run(executor._save_all_drafts(rows, round_id, report))

    verification = conn.execute(
        "SELECT status, error_msg FROM draft_save_verifications WHERE draft_id=?",
        (draft_id,),
    ).fetchone()
    assert verification is not None
    assert verification["status"] == "pending_verify"
    assert "internal.example" not in verification["error_msg"]
    assert "secret" not in verification["error_msg"]
    assert "结果不确定" in verification["error_msg"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0] == "draft_pending"
    assert report["drafts_needs_human_verify"] == 1
    assert report["drafts_failed"] == 0

    try:
        asyncio.run(executor.save_pending_drafts(round_id))
    except ValueError as exc:
        assert "没有待存草稿" in str(exc)
    else:
        raise AssertionError("adapter exception must not be retried automatically")
    assert adapter.calls == 1


def test_uncertain_draft_save_is_not_retried_and_remains_for_human_verification(
    cfg, conn, seed, monkeypatch,
):
    from src.adapters.base import DraftResult
    from src.core import login_manager

    monkeypatch.setattr(login_manager, "has_login_state", lambda _platform: True)
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )

    class UncertainAdapter:
        def __init__(self):
            self.calls = 0

        async def save_draft(self, payload):
            self.calls += 1
            return DraftResult(
                success=False,
                needs_human_verify=True,
                platform_draft_id=None,
                platform_draft_id_missing=True,
                error_msg="点击保存后未取得平台确认信号",
            )

    adapter = UncertainAdapter()
    executor = _executor(cfg, conn, {Platform.XHS: adapter})
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0]
    report = {"drafts_failed": 0, "drafts_saved": 0, "steps": []}
    rows = [{
        "content_id": content_id,
        "draft_id": draft_id,
        "platform": Platform.XHS,
        "title": "不确定保存结果",
        "body": "测试正文",
        "tags": [],
        "image_paths": [],
    }]

    asyncio.run(executor._save_all_drafts(rows, round_id, report))

    verification = conn.execute(
        "SELECT status, platform_draft_id FROM draft_save_verifications WHERE draft_id=?",
        (draft_id,),
    ).fetchone()
    assert verification is not None
    assert verification["status"] == "pending_verify"
    assert verification["platform_draft_id"] is None
    assert conn.execute(
        "SELECT status FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0] == "draft_pending"
    assert report["drafts_needs_human_verify"] == 1
    assert report["drafts_failed"] == 0

    # Recovery must not submit the same platform save a second time until a human
    # has explicitly confirmed that the first attempt did not create a draft.
    try:
        asyncio.run(executor.save_pending_drafts(round_id))
    except ValueError as exc:
        assert "没有待存草稿" in str(exc)
    else:
        raise AssertionError("pending_verify draft must be excluded from automatic retry")
    assert adapter.calls == 1


def test_round_failure_classification_preserves_actionable_categories():
    from src.core.round_executor import _classify_round_failure

    class ImageGenerationError(RuntimeError):
        pass

    class BrowserError(RuntimeError):
        pass

    assert _classify_round_failure(TimeoutError("provider timed out"))[0] == "timeout"
    assert _classify_round_failure(FileNotFoundError("asset missing"))[0] == "asset_missing"
    assert _classify_round_failure(ImageGenerationError("model rejected request"))[0] == "image_generation_failed"
    assert _classify_round_failure(BrowserError("browser closed"))[0] == "browser_save_failed"
    assert _classify_round_failure(RuntimeError("unexpected"))[0] == "pipeline_error"


def test_interrupted_draft_save_is_marked_before_external_side_effect(cfg, conn, seed, monkeypatch):
    """Worker cancellation after request dispatch must not leave an auto-retryable draft."""
    import asyncio
    from src.adapters.base import Platform
    from src.core import login_manager

    monkeypatch.setattr(login_manager, "has_login_state", lambda _platform: True)
    round_id = seed.round(status="producing")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    content_id = conn.execute("SELECT content_id FROM drafts WHERE id=?", (draft_id,)).fetchone()[0]

    class CancelAfterDispatchAdapter:
        async def save_draft(self, payload):
            marker = conn.execute(
                "SELECT status,error_msg FROM draft_save_verifications WHERE draft_id=?", (draft_id,),
            ).fetchone()
            assert marker is not None and marker["status"] == "pending_verify"
            assert "尝试已启动" in marker["error_msg"]
            raise asyncio.CancelledError()

    executor = _executor(cfg, conn, {Platform.XHS: CancelAfterDispatchAdapter()})
    rows = [{
        "content_id": content_id, "draft_id": draft_id, "platform": Platform.XHS,
        "title": "中断恢复测试", "body": "正文", "tags": [], "image_paths": [],
    }]
    try:
        asyncio.run(executor._save_all_drafts(rows, round_id, {"drafts_failed": 0, "drafts_saved": 0, "steps": []}))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("simulated worker cancellation must propagate")

    verification = conn.execute(
        "SELECT status,error_msg FROM draft_save_verifications WHERE draft_id=?", (draft_id,),
    ).fetchone()
    assert verification["status"] == "pending_verify"
    assert "尝试已启动" in verification["error_msg"]
    assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == "draft_pending"


def test_restart_preserves_pending_drafts_when_production_tasks_already_succeeded(cfg, conn, seed):
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,),
    ).fetchone()[0]
    # This fixture tests recovery/timeout decisions, not task creation provenance.
    # Disable table-local guards only in this isolated fixture; production migrations
    # and their trigger behavior remain covered by the migration/task-store tests.
    for trigger in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name IN ('production_tasks', 'content_production_line')"
    ).fetchall():
        trigger_name = str(trigger["name"]).replace('"', '""')
        conn.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
    conn.execute(
        """INSERT INTO production_tasks(
            id,idempotency_key,round_id,master_plan_version_id,sub_plan_id,
            sub_plan_version_id,strategy_step_id,platform,task_ordinal,status,
            attempt_count,max_attempts,content_id,input_snapshot_json
        ) VALUES('pt-succeeded','round:step:xhs:1',?,1,'sub-a',1,'step-a',
                 'xhs',1,'succeeded',1,3,?,'{}')""",
        (round_id, content_id),
    )
    conn.commit()

    RecoveryManager(cfg, conn)._stale_producing_rounds()

    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,),
    ).fetchone()[0] == "producing"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,),
    ).fetchone()[0] == "draft_pending"


def test_finalize_dry_run_advances_round_with_pending_drafts(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    executor = _executor(cfg, conn)

    report = {"steps": [], "errors": []}
    executor._finalize_round(round_id, report, save_drafts=False)

    assert report["final_status"] == "awaiting_publish"
    assert any(step.get("step") == "dry_run" for step in report["steps"])
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "awaiting_publish"
    assert conn.execute(
        "SELECT status FROM drafts WHERE content_id IN "
        "(SELECT id FROM contents WHERE round_id = ?)", (round_id,)
    ).fetchone()[0] == "draft_pending"


def test_finalize_save_drafts_mode_keeps_pending_round_producing(cfg, conn, seed):
    round_id = seed.round(status="producing")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    executor = _executor(cfg, conn)

    report = {"steps": [], "errors": []}
    executor._finalize_round(round_id, report, save_drafts=True)

    assert report["final_status"] == "producing"
    assert report["errors"]
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "producing"
