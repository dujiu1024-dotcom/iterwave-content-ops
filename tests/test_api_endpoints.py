"""R-2/R-3/R-4/R-5/R-6 新接口的 HTTP 级用例。

不跑 lifespan（避免真实 config/data 目录），直接把 tmp 库里的模块塞进 `_g_ctx`，
再用 TestClient 发真实请求，验证状态码与落库结果。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.notify import Notifier
from src.core.metrics import Normalizer
from src.core.ops.pause import is_ops_paused
from src.core.plan_version import PlanVersionManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine
from src.core.task_queue import GlobalTaskQueue
from src.core.task_worker import GlobalTaskWorker


@pytest.fixture()
def client(cfg, conn, seed, test_auth):
    """注入临时依赖的 TestClient（不执行 lifespan），并建立受保护会话。"""
    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "cfg": cfg,
        "conn": conn,
        "sm_draft": DraftStateMachine(conn),
        "sm_round": RoundStateMachine(conn),
        "plan_version_manager": PlanVersionManager(cfg, conn),
        "notifier": Notifier(cfg, conn),
        "normalizer": Normalizer(conn, cfg),
    })
    app = api_main._build_app()
    try:
        tc = test_auth(TestClient(app, base_url="http://127.0.0.1"))
        yield tc, conn, seed
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


# ---------------------------------------------------------------- R-2 采纳缺口
def test_rhythm_config_exposes_quota_slots_for_all_supported_platforms(client):
    tc, _, _ = client
    response = tc.get("/api/rhythm")
    assert response.status_code == 200
    body = response.json()
    supported = {"xhs", "douyin", "kuaishou", "wechat_channels"}
    assert set(body["quotas"]) == supported
    assert set(body["posting_windows"]) == supported
    for quota in body["quotas"].values():
        assert all(isinstance(quota[key], int) and quota[key] >= 0 for key in (
            "passive_replies", "active_comments", "follows",
        ))

def test_tenant_write_requires_explicit_context_and_rejects_stale_context(client):
    tc, conn, _ = client
    before = conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0]

    tc.headers.pop("X-Tenant-Id", None)
    missing = tc.post("/api/rounds", json={"topic_count": 3})
    assert missing.status_code == 409
    assert missing.json()["code"] == "TENANT_CONTEXT_REQUIRED"

    tc.headers.update({"X-Tenant-Id": "tenant-b"})
    stale = tc.post("/api/rounds", json={"topic_count": 3})
    assert stale.status_code == 409
    assert stale.json()["code"] == "TENANT_CONTEXT_CHANGED"
    assert api_main._tenant_switch_gate.active_requests == 0
    assert conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0] == before


def _seed_active_queue_round(tmp_path, monkeypatch, round_id: int):
    """用持久化队列记录模拟运行中轮次，不再依赖进程级轮次集合。"""
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    queue.enqueue(
        task_type="round_run", tenant_id="default", aggregate_id=str(round_id),
        payload={"round_id": round_id, "operation": "run"},
    )
    claimed = queue.claim_next("test-api-worker")
    assert claimed is not None
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setitem(api_main._g_ctx, "runtime_tenant_id", "default")


def test_create_round_without_saved_master_plan_succeeds_without_ghost_round(client):
    tc, conn, _ = client
    response = tc.post("/api/rounds", json={"topic_count": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    row = conn.execute(
        "SELECT id, plan_signature FROM spiral_rounds WHERE id = ?", (body["id"],)
    ).fetchone()
    assert row is not None and row["plan_signature"]
    assert conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM signature_history WHERE round_id = ?", (body["id"],)
    ).fetchone()[0] == 1


def test_create_round_rolls_back_when_plan_signature_fails(client, monkeypatch):
    tc, conn, _ = client
    monkeypatch.setattr(
        PlanVersionManager, "record",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("signature write failed")),
    )
    with pytest.raises(RuntimeError, match="signature write failed"):
        tc.post("/api/rounds", json={"topic_count": 6})
    assert conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0] == 0


def test_adopt_gaps_rejects_empty(client):
    tc, _, _ = client
    assert tc.post("/api/plan_adjustments/adopt", json={"dimensions": []}).status_code == 400


def test_adopt_gaps_writes_plan_and_changes_signature(client):
    tc, conn, _ = client
    pvm = PlanVersionManager(api_main._g_ctx["cfg"], conn)
    before = pvm.load_plan()[1]

    r = tc.post("/api/plan_adjustments/adopt", json={"dimensions": ["政策解读", "家庭场景"]})
    assert r.status_code == 200
    body = r.json()
    assert body["adopted_dimensions"] == ["政策解读", "家庭场景"]
    assert body["dimension_allocation"]["政策解读"] == 1
    assert body["signature"] != before

    # 签名已持久化到 signature_history（R18 审计追溯）
    rows = conn.execute("SELECT COUNT(*) FROM signature_history").fetchone()[0]
    assert rows >= 1


# ---------------------------------------------------------------- R-3 热点入库
def test_hot_import_requires_evidence(client):
    tc, _, _ = client
    # R22：缺 url → 400
    r = tc.post("/api/hot_topics/import", json={"title": "无链接热点", "source": "weibo"})
    assert r.status_code == 400
    # 缺 source → 400
    r = tc.post("/api/hot_topics/import", json={"title": "无来源", "url": "https://x.com/a"})
    assert r.status_code == 400


def test_hot_import_is_idempotent_by_source_title(client):
    tc, conn, _ = client
    payload = {"title": "秋季流感高发", "source": "weibo",
               "url": "https://s.weibo.com/1", "dimension": "健康"}
    first = tc.post("/api/hot_topics/import", json=payload)
    assert first.status_code == 200 and first.json()["imported"] is True

    second = tc.post("/api/hot_topics/import", json=payload)
    assert second.status_code == 200 and second.json()["imported"] is False

    row = conn.execute(
        "SELECT id, dimension FROM topic_index WHERE title = ?", ("秋季流感高发",)
    ).fetchone()
    assert row is not None
    assert row["id"].startswith("hot-")
    assert row["dimension"] == "健康"


# ---------------------------------------------------------------- R-6 全局暂停
def test_ops_pause_roundtrip_persists(client):
    tc, conn, _ = client
    assert tc.get("/api/ops/pause").json()["paused"] is False
    assert tc.post("/api/ops/pause", json={"paused": True}).json()["paused"] is True
    assert is_ops_paused(conn) is True
    assert tc.get("/api/ops/pause").json()["paused"] is True
    assert tc.post("/api/ops/pause", json={"paused": False}).json()["paused"] is False
    assert is_ops_paused(conn) is False


# ---------------------------------------------------------------- R-4 通知自检
def test_notify_test_reports_channels(client):
    tc, _, _ = client
    r = tc.post("/api/notify/test")
    assert r.status_code == 200
    results = r.json()["results"]
    assert set(results) == {"macos", "wecom", "feishu"}
    assert results["macos"] in {"ok", "failed", "disabled", "unavailable"}


def test_notify_test_503_when_notifier_missing(client):
    tc, _, _ = client
    api_main._g_ctx["notifier"] = None
    assert tc.post("/api/notify/test").status_code == 503


def test_editing_content_invalidates_previous_guard_result(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_saved", fetch_status=None)
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE contents SET guard_outcome = 'pass', guard_reason = '原稿通过' WHERE id = ?",
        (content_id,),
    )
    conn.commit()

    unchanged = tc.patch(f"/api/contents/{content_id}", json={"title": "T"})
    assert unchanged.status_code == 200 and unchanged.json()["changed"] is False
    row = conn.execute(
        "SELECT guard_outcome, guard_reason FROM contents WHERE id = ?", (content_id,)
    ).fetchone()
    assert row["guard_outcome"] == "pass"

    edited = tc.patch(
        f"/api/contents/{content_id}",
        json={"body": "编辑后的正文", "reason": "测试修改"},
    )
    assert edited.status_code == 200 and edited.json()["changed"] is True
    row = conn.execute(
        "SELECT guard_outcome, guard_reason FROM contents WHERE id = ?", (content_id,)
    ).fetchone()
    assert row["guard_outcome"] is None
    assert row["guard_reason"] is None


def test_published_content_cannot_be_mutated_in_place(client):
    tc, conn, seed = client
    cases = [
        ("user_published", None),
        # 数据采集失败会把草稿状态改成 failed，但确认发布时间必须继续保护历史文案。
        ("failed", "2026-01-01T00:00:00"),
    ]
    for index, (status, confirmed_at) in enumerate(cases):
        round_id = seed.round(status="collecting_metrics")
        draft_id = seed.draft(
            round_id, platform="xhs", draft_status=status, fetch_status=None,
            topic_id=f"immutability-{index}",
        )
        if confirmed_at:
            conn.execute(
                "UPDATE drafts SET user_confirmed_at = ? WHERE id = ?",
                (confirmed_at, draft_id),
            )
            conn.commit()
        content_id = conn.execute(
            "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()[0]

        response = tc.patch(
            f"/api/contents/{content_id}",
            json={"title": "不应覆盖已发布版本", "reason": "回归测试"},
        )
        assert response.status_code == 409
        row = conn.execute(
            "SELECT title, body FROM contents WHERE id = ?", (content_id,)
        ).fetchone()
        assert row["title"] == "T"
        assert row["body"] == "B"


def test_failed_metrics_fetch_does_not_make_published_history_deletable(client):
    tc, conn, seed = client
    round_id = seed.round(status="partial_completed")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="failed",
        fetch_status=None, topic_id="published-history",
    )
    conn.execute(
        "UPDATE drafts SET user_confirmed_at = ?, error_msg = ? WHERE id = ?",
        ("2026-01-01T00:00:00", "数据采集重试耗尽", draft_id),
    )
    conn.commit()
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]

    delete_content_response = tc.delete(f"/api/contents/{content_id}")
    assert delete_content_response.status_code == 409
    assert conn.execute(
        "SELECT 1 FROM contents WHERE id = ?", (content_id,)
    ).fetchone() is not None

    delete_round_response = tc.delete(f"/api/rounds/{round_id}")
    assert delete_round_response.status_code == 409
    assert conn.execute(
        "SELECT 1 FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone() is not None


def test_round_business_states_cannot_be_set_manually(client):
    tc, conn, seed = client
    created_round = seed.round(status="created")
    response = tc.post(
        f"/api/rounds/{created_round}/transition",
        json={"target_status": "producing", "reason": "回归测试"},
    )
    assert response.status_code == 409
    assert "业务流程自动推进" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (created_round,)
    ).fetchone()[0] == "created"

    producing_round = seed.round(status="producing")
    seed.draft(
        producing_round, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    response = tc.post(
        f"/api/rounds/{producing_round}/transition",
        json={"target_status": "awaiting_publish", "reason": "跳过存草稿"},
    )
    assert response.status_code == 409
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (producing_round,)
    ).fetchone()[0] == "producing"


def test_round_cancel_rejected_while_run_is_active(client, tmp_path, monkeypatch):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    _seed_active_queue_round(tmp_path, monkeypatch, round_id)
    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "回归测试"},
    )

    assert response.status_code == 409
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "producing"


def test_round_cancel_rejected_while_platform_draft_is_saved(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved", fetch_status=None,
    )
    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "回归测试"},
    )

    assert response.status_code == 409
    assert "已存入平台" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "awaiting_publish"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"


def test_round_cancel_rejected_if_published_history_exists_even_when_failed(client):
    tc, conn, seed = client
    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="failed", fetch_status=None,
    )
    conn.execute(
        "UPDATE drafts SET user_confirmed_at = ?, error_msg = ? WHERE id = ?",
        ("2026-01-01T00:00:00", "采集重试耗尽", draft_id),
    )
    conn.commit()

    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "回归测试"},
    )

    assert response.status_code == 409
    assert "存在发布历史" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "collecting_metrics"


def test_cancel_round_cancels_local_pending_drafts_together(client):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "用户取消本轮"},
    )

    assert response.status_code == 200
    assert response.json()["new_status"] == "cancelled"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "cancelled"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "cancelled"


def test_round_cancel_rejected_while_run_is_active(client, tmp_path, monkeypatch):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    _seed_active_queue_round(tmp_path, monkeypatch, round_id)
    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "回归测试"},
    )

    assert response.status_code == 409
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "producing"


def test_round_cancel_rejected_while_platform_draft_is_saved(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved", fetch_status=None,
    )
    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "回归测试"},
    )

    assert response.status_code == 409
    assert "已存入平台" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "awaiting_publish"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"


def test_round_cancel_rejected_if_published_history_exists_even_when_failed(client):
    tc, conn, seed = client
    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="failed", fetch_status=None,
    )
    conn.execute(
        "UPDATE drafts SET user_confirmed_at = ?, error_msg = ? WHERE id = ?",
        ("2026-01-01T00:00:00", "采集重试耗尽", draft_id),
    )
    conn.commit()

    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "回归测试"},
    )

    assert response.status_code == 409
    assert "存在发布历史" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "collecting_metrics"


def test_cancel_round_cancels_local_pending_drafts_together(client):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    response = tc.post(
        f"/api/rounds/{round_id}/transition",
        json={"target_status": "cancelled", "reason": "用户取消本轮"},
    )

    assert response.status_code == 200
    assert response.json()["new_status"] == "cancelled"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "cancelled"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "cancelled"


def test_publish_confirmation_requires_current_quality_gate_or_audited_override(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE drafts SET saved_content_hash = ? WHERE id = ?",
        (_current_platform_payload_hash(conn, content_id), draft_id),
    )
    conn.commit()
    payload = {"note_url": "https://www.xiaohongshu.com/explore/123"}

    # 当前文案未检查：普通确认被拦截，不改变发布/轮次状态。
    response = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json=payload)
    assert response.status_code == 409
    assert "未检查" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"

    # 当前质检失败：没有明确例外原因不能静默放行。
    conn.execute(
        "UPDATE contents SET guard_outcome = 'fail', guard_reason = '红线命中' WHERE id = ?",
        (content_id,),
    )
    conn.commit()
    response = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json=payload)
    assert response.status_code == 409
    assert "红线命中" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"

    # 如果发布确实已在外部平台发生，用户可提供显式原因登记；状态闭环不丢失，且写入审计。
    override_payload = {
        **payload,
        "quality_override_reason": "实际已发布，保留真实运营数据",
    }
    response = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json=override_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "user_published"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "user_published"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "collecting_metrics"
    audit = conn.execute(
        "SELECT override_field, reason, overridden_by FROM user_override "
        "WHERE round_id = ? ORDER BY id DESC LIMIT 1", (round_id,)
    ).fetchone()
    assert audit is not None
    assert audit["override_field"] == "publish_quality_override"
    assert audit["reason"] == "实际已发布，保留真实运营数据"
    assert audit["overridden_by"] == "publish-confirm-ui"

    # 另一个草稿的当前质量门通过后，可走普通确认，无需例外理由。
    other_round = seed.round(status="awaiting_publish")
    other_id = seed.draft(
        other_round, platform="xhs", draft_status="draft_saved",
        fetch_status=None, topic_id="pass-quality",
    )
    other_content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (other_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE drafts SET saved_content_hash = ? WHERE id = ?",
        (_current_platform_payload_hash(conn, other_content_id), other_id),
    )
    conn.execute(
        "UPDATE contents SET guard_outcome = 'pass', guard_reason = '检查通过' WHERE id = ?",
        (other_content_id,),
    )
    conn.commit()
    response = tc.post(f"/api/drafts/{other_id}/publish_confirm", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "user_published"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (other_round,)
    ).fetchone()[0] == "collecting_metrics"


def test_dry_run_content_can_be_manually_published_and_confirmation_is_idempotent(client):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id,
        platform="xhs",
        draft_status="draft_pending",
        fetch_status=None,
        topic_id="manual-publish-from-dry-run",
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE contents SET guard_outcome = 'pass', guard_reason = '当前文案检查通过' WHERE id = ?",
        (content_id,),
    )
    conn.commit()
    payload = {"note_url": "https://www.xiaohongshu.com/explore/manual-post"}

    # 干跑内容没有平台草稿，但用户手动发布后可登记，进入正常采集流程。
    response = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "user_published"
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "user_published"
    assert conn.execute(
        "SELECT status FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == "collecting_metrics"

    transition_log_before = conn.execute(
        "SELECT transition_log FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0]
    confirmed_at_before = conn.execute(
        "SELECT user_confirmed_at FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]

    # 重复提交时返回当前状态，不再覆盖确认时间、不重复推进轮次或重新触发发布副作用。
    response = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "user_published"
    assert response.json()["already_confirmed"] is True
    assert conn.execute(
        "SELECT transition_log FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone()[0] == transition_log_before
    assert conn.execute(
        "SELECT user_confirmed_at FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == confirmed_at_before


def test_manual_rejection_blocks_normal_publish_until_resubmitted_or_overridden(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE drafts SET saved_content_hash = ? WHERE id = ?",
        (_current_platform_payload_hash(conn, content_id), draft_id),
    )
    conn.execute(
        "UPDATE contents SET guard_outcome = 'pass', guard_reason = 'G2 通过' WHERE id = ?",
        (content_id,),
    )
    conn.commit()

    rejected = tc.post(
        f"/api/contents/{content_id}/reject",
        json={"reason": "观点不符合账号定位"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_rejected"] is True
    content = conn.execute(
        "SELECT review_rejected, review_rejection_reason FROM contents WHERE id = ?",
        (content_id,),
    ).fetchone()
    assert content["review_rejected"] == 1
    assert content["review_rejection_reason"] == "观点不符合账号定位"
    assert conn.execute(
        "SELECT error_msg FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0].startswith("[已驳回]")

    listed = tc.get("/api/drafts").json()
    draft = next(item for item in listed if item["id"] == draft_id)
    assert draft["review_rejected"] is True
    assert draft["guard_confirmation_allowed"] is False

    normal_confirm = tc.post(
        f"/api/drafts/{draft_id}/publish_confirm",
        json={"note_url": "https://www.xiaohongshu.com/explore/rejected"},
    )
    assert normal_confirm.status_code == 409
    assert "已被人工驳回" in normal_confirm.json()["detail"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"

    # 只有明确说明“外部平台已真实发布”的例外原因，才能登记真实发布与采集。
    override = tc.post(
        f"/api/drafts/{draft_id}/publish_confirm",
        json={
            "note_url": "https://www.xiaohongshu.com/explore/rejected",
            "quality_override_reason": "平台实际已发布，保留真实运营记录",
        },
    )
    assert override.status_code == 200
    assert override.json()["status"] == "user_published"
    audit = conn.execute(
        "SELECT override_field, reason FROM user_override "
        "WHERE round_id = ? ORDER BY id DESC LIMIT 1", (round_id,)
    ).fetchone()
    assert audit["override_field"] == "publish_review_rejection_override"
    assert audit["reason"] == "平台实际已发布，保留真实运营记录"


def test_rejected_content_must_pass_g2_before_resubmission(client):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    rejected = tc.post(
        f"/api/contents/{content_id}/reject",
        json={"reason": "需要重新论证"},
    )
    assert rejected.status_code == 200

    blocked = tc.post(
        f"/api/contents/{content_id}/resubmit",
        json={"reason": "我已修改"},
    )
    assert blocked.status_code == 409
    assert "须先修改文案并重新检查通过" in blocked.json()["detail"]
    assert conn.execute(
        "SELECT review_rejected FROM contents WHERE id = ?", (content_id,)
    ).fetchone()[0] == 1

    conn.execute(
        "UPDATE contents SET guard_outcome = 'pass', guard_reason = '重新检查通过' WHERE id = ?",
        (content_id,),
    )
    conn.commit()
    resubmitted = tc.post(
        f"/api/contents/{content_id}/resubmit",
        json={"reason": "已修改并通过目标检查"},
    )
    assert resubmitted.status_code == 200
    row = conn.execute(
        "SELECT review_rejected, review_rejection_reason FROM contents WHERE id = ?",
        (content_id,),
    ).fetchone()
    assert row["review_rejected"] == 0
    assert row["review_rejection_reason"] is None
    assert conn.execute(
        "SELECT error_msg FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_pending"
    audit = conn.execute(
        "SELECT override_field, reason FROM user_override "
        "WHERE round_id = ? ORDER BY id DESC LIMIT 1", (round_id,)
    ).fetchone()
    assert audit["override_field"] == "review_resubmit"
    assert audit["reason"] == "已修改并通过目标检查"


def test_content_mutations_are_rejected_while_round_is_running(client, tmp_path, monkeypatch):
    tc, conn, seed = client
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    _seed_active_queue_round(tmp_path, monkeypatch, round_id)
    edit = tc.patch(
        f"/api/contents/{content_id}",
        json={"title": "并发修改", "reason": "回归测试"},
    )
    reject = tc.post(
        f"/api/contents/{content_id}/reject",
        json={"reason": "并发驳回"},
    )
    delete = tc.delete(f"/api/contents/{content_id}")

    assert edit.status_code == 409
    assert reject.status_code == 409
    assert delete.status_code == 409
    row = conn.execute(
        "SELECT title, review_rejected FROM contents WHERE id = ?", (content_id,)
    ).fetchone()
    assert row["title"] == "T"
    assert row["review_rejected"] == 0
    assert conn.execute(
        "SELECT 1 FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone() is not None


def test_published_content_cannot_be_manually_rejected(client):
    tc, conn, seed = client
    round_id = seed.round(status="collecting_metrics")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="user_published", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    response = tc.post(
        f"/api/contents/{content_id}/reject",
        json={"reason": "不应覆盖已发布历史"},
    )
    assert response.status_code == 409
    assert conn.execute(
        "SELECT review_rejected FROM contents WHERE id = ?", (content_id,)
    ).fetchone()[0] == 0


def _current_platform_payload_hash(conn, content_id: str) -> str:
    from src.core.content_version import content_version_hash, parse_json_list

    row = conn.execute(
        "SELECT title, body, tags, image_paths FROM contents WHERE id = ?",
        (content_id,),
    ).fetchone()
    images = parse_json_list(row["image_paths"])
    payload_images = [str(images[0])] if images and images[0] else []
    return content_version_hash(
        row["title"], row["body"], parse_json_list(row["tags"]), payload_images,
    )


def test_saved_platform_draft_version_mismatch_requires_explicit_override(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    old_hash = _current_platform_payload_hash(conn, content_id)
    conn.execute(
        "UPDATE drafts SET saved_content_hash = ? WHERE id = ?", (old_hash, draft_id)
    )
    conn.execute(
        "UPDATE contents SET title = ?, guard_outcome = 'pass', guard_reason = ? WHERE id = ?",
        ("本地改过的新标题", "G2 复检通过", content_id),
    )
    conn.commit()

    listed = tc.get("/api/drafts").json()
    item = next(d for d in listed if d["id"] == draft_id)
    assert item["platform_content_sync"] == "stale"
    assert item["guard_confirmation_allowed"] is True
    # 列表 API 只返回版本状态，不返回正文、原始标签、绝对图片路径或内部指纹。
    assert "body" not in item
    assert "tags" not in item
    assert "image_paths" not in item
    assert "saved_content_hash" not in item
    assert "saved_platform_title" not in item

    response = tc.post(
        f"/api/drafts/{draft_id}/publish_confirm",
        json={"note_url": "https://www.xiaohongshu.com/explore/old-version"},
    )
    assert response.status_code == 409
    assert "本地文案已不同于上次存入平台的版本" in response.json()["detail"]
    assert conn.execute(
        "SELECT status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0] == "draft_saved"

    # 若外部发布事实上已发生，保留真实采集，但要求单独说明平台版本差异并审计。
    response = tc.post(
        f"/api/drafts/{draft_id}/publish_confirm",
        json={
            "note_url": "https://www.xiaohongshu.com/explore/old-version",
            "platform_sync_override_reason": "实际发布的是平台旧版本，保留真实运营数据",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "user_published"
    audit = conn.execute(
        "SELECT override_field, reason FROM user_override "
        "WHERE round_id = ? ORDER BY id DESC LIMIT 1", (round_id,)
    ).fetchone()
    assert audit["override_field"] == "platform_content_sync_override"
    assert audit["reason"] == "实际发布的是平台旧版本，保留真实运营数据"


def test_legacy_platform_draft_requires_sync_attestation_before_normal_confirm(client):
    tc, conn, seed = client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_saved", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE drafts SET saved_content_hash = NULL WHERE id = ?", (draft_id,)
    )
    conn.execute(
        "UPDATE contents SET guard_outcome = 'pass', guard_reason = 'G2 通过' WHERE id = ?",
        (content_id,),
    )
    conn.commit()

    listed = tc.get("/api/drafts").json()
    item = next(d for d in listed if d["id"] == draft_id)
    assert item["platform_content_sync"] == "unknown"
    blocked = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json={})
    assert blocked.status_code == 409
    assert "没有保存版本指纹" in blocked.json()["detail"]

    synced = tc.post(
        f"/api/drafts/{draft_id}/confirm_platform_sync",
        json={"reason": "已人工核对平台草稿并更新为当前版本"},
    )
    assert synced.status_code == 200
    assert synced.json()["platform_content_sync"] == "matched"
    expected_hash = _current_platform_payload_hash(conn, content_id)
    saved_hash = conn.execute(
        "SELECT saved_content_hash FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    assert saved_hash == expected_hash
    saved_title = conn.execute(
        "SELECT saved_platform_title FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    current_title = conn.execute(
        "SELECT title FROM contents WHERE id = ?", (content_id,)
    ).fetchone()[0]
    assert saved_title == current_title

    confirmed = tc.post(f"/api/drafts/{draft_id}/publish_confirm", json={})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "user_published"
    audit = conn.execute(
        "SELECT override_field, reason FROM user_override "
        "WHERE round_id = ? AND override_field = 'platform_content_sync_confirmed' "
        "ORDER BY id DESC LIMIT 1", (round_id,)
    ).fetchone()
    assert audit is not None
    assert audit["reason"] == "已人工核对平台草稿并更新为当前版本"


# ---------------------------------------------------------------- R-5 KPI 窗口
def test_ops_kpi_days_param(client):
    tc, _, seed = client
    rid = seed.round("collecting_metrics")
    seed.draft(rid, platform="xhs")
    ok = tc.get("/api/ops_kpi", params={"days": 7})
    assert ok.status_code == 200 and ok.json()["window_days"] == 7
    # 越界参数由 Query(ge=1, le=365) 拦下
    assert tc.get("/api/ops_kpi", params={"days": 0}).status_code == 422
    assert tc.get("/api/ops_kpi", params={"days": 999}).status_code == 422


def test_global_todo_queue_fetches_beyond_first_500_records(client, cfg, tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    tc, _, _ = client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    queue_conn = queue._connect()
    queue_conn.executemany(
        "INSERT INTO global_task_queue "
        "(queue_order, task_id, task_type, tenant_id, payload_json, status, created_at, "
        "finished_at, error_code, error_message) "
        "VALUES (?, ?, 'scheduler_job', 'default', '{}', 'failed', ?, ?, 'TEST', '任务失败')",
        [(index, f"failed-{index}", f"2026-11-01T12:{index // 60:02d}:{index % 60:02d}",
          f"2026-11-01T12:{index // 60:02d}:{index % 60:02d}") for index in range(1, 502)],
    )
    queue_conn.commit()
    queue_conn.close()

    db_file = Path(cfg.resolve_path(cfg.paths.db_file))

    class Registry:
        def list(self):
            return [{"id": "default", "name": "测试账号组"}]

        def paths(self, _tenant_id, name=None):
            return SimpleNamespace(db_file=db_file)

    monkeypatch.setitem(api_main._g_ctx, "tenant_registry", Registry())
    monkeypatch.setattr(api_main, "_task_queue", queue)

    response = tc.get("/api/todos?page=26&page_size=20&source=global_queue")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 501
    assert body["pages"] == 26
    assert len(body["items"]) == 1
    assert body["items"][0]["record_id"] == "failed-1"
    assert body["capped"] is False


def test_operations_history_filters_by_platform_status_type_and_date(client):
    tc, conn, _ = client
    conn.executemany(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("xhs", "active_comment", "小红书失败日志", "2026-10-30 10:00:00", "failed"),
            ("xhs", "follow", "小红书待核实日志", "2026-11-01 10:00:00", "pending_verify"),
            ("douyin", "active_comment", "抖音成功日志", "2026-11-01 11:00:00", "success"),
        ],
    )
    conn.executemany(
        "INSERT INTO task_runs (task_type, platform, status, created_at, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("comment_polling", "xhs", "failed", "2026-10-30 10:00:00", "2026-10-30 10:00:00"),
            ("data_fetch", "douyin", "success", "2026-11-01 11:00:00", "2026-11-01 11:00:00"),
            ("comment_polling", "xhs", "pending_verify", "2026-11-01 12:00:00", "2026-11-01 12:00:00"),
        ],
    )
    conn.commit()

    logs = tc.get("/api/ops_logs", params={
        "page": 1, "page_size": 20, "platform": "xhs",
        "status": "pending_verify", "date_from": "2026-11-01",
    })
    assert logs.status_code == 200
    assert logs.json()["total"] == 1
    assert logs.json()["items"][0]["content"] == "小红书待核实日志"

    runs = tc.get("/api/task_runs", params={
        "page": 1, "page_size": 20, "platform": "xhs",
        "status": "pending_verify", "task_type": "comment_polling",
        "date_from": "2026-11-01", "date_to": "2026-11-01",
    })
    assert runs.status_code == 200
    assert runs.json()["total"] == 1
    assert runs.json()["items"][0]["platform"] == "xhs"

    assert tc.get("/api/ops_logs", params={"date_from": "11-01-2026"}).status_code == 422
    assert tc.get("/api/task_runs", params={"date_from": "2026-11-02", "date_to": "2026-11-01"}).status_code == 422
    assert tc.get("/api/task_runs", params={"platform": "unknown"}).status_code == 422


# ---------------------------------------------------------------- R-17 pending_verify
def test_resolve_pending_verify_branching(client):
    tc, conn, _ = client
    conn.execute(
        "INSERT INTO task_runs (task_type, status, started_at) VALUES ('comment_polling', 'pending_verify', ?)",
        ("2026-10-28 07:00:00",),
    )
    conn.commit()
    run_id = conn.execute("SELECT MAX(id) FROM task_runs").fetchone()[0]

    assert tc.post(f"/api/task_runs/{run_id}/resolve", json={"action": "bogus"}).status_code == 400
    r = tc.post(f"/api/task_runs/{run_id}/resolve", json={"action": "sent"})
    assert r.status_code == 200
    assert r.json()["already_resolved"] is False
    row = conn.execute(
        "SELECT status, resolved_at, resolved_by, resolution_action FROM task_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "success"
    assert row["resolved_at"] and row["resolved_by"].startswith("session:")
    assert row["resolution_action"] == "sent"
    repeated = tc.post(f"/api/task_runs/{run_id}/resolve", json={"action": "sent"})
    assert repeated.status_code == 200 and repeated.json()["already_resolved"] is True
    assert tc.post(f"/api/task_runs/{run_id}/resolve", json={"action": "not_sent"}).status_code == 409


def test_resolve_pending_ops_log_is_individual_audited_and_idempotent(client):
    tc, conn, _ = client
    conn.executemany(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
        "VALUES ('xhs', 'active_comment', ?, '2026-11-01 12:00:00', 'pending_verify')",
        [("待核实日志 A",), ("待核实日志 B",), ("待核实日志 C",)],
    )
    conn.commit()
    ids = [row[0] for row in conn.execute(
        "SELECT id FROM ops_logs ORDER BY id DESC LIMIT 3"
    ).fetchall()]
    target_id = ids[0]

    resolved = tc.post(f"/api/ops_logs/{target_id}/resolve", json={"action": "sent"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "success"
    row = conn.execute(
        "SELECT status, resolved_at, resolved_by, resolution_action, resolution_note "
        "FROM ops_logs WHERE id = ?", (target_id,),
    ).fetchone()
    assert row["status"] == "success"
    assert row["resolved_at"] and row["resolved_by"].startswith("session:")
    assert row["resolution_action"] == "sent"
    assert row["resolution_note"] == "用户核实：已发出"
    assert conn.execute(
        "SELECT COUNT(*) FROM ops_logs WHERE status = 'pending_verify' AND platform = 'xhs'"
    ).fetchone()[0] == 2

    repeated = tc.post(f"/api/ops_logs/{target_id}/resolve", json={"action": "sent"})
    assert repeated.status_code == 200 and repeated.json()["already_resolved"] is True
    assert tc.post(f"/api/ops_logs/{target_id}/resolve", json={"action": "not_sent"}).status_code == 409
    assert tc.post("/api/ops_logs/999999/resolve", json={"action": "sent"}).status_code == 404


def test_resolving_linked_ops_log_updates_only_its_task_run(client):
    tc, conn, _ = client
    task_id = conn.execute(
        "INSERT INTO task_runs (task_type, status, platform, started_at) "
        "VALUES ('comment_polling', 'pending_verify', 'xhs', ?)",
        ("2026-11-01 10:00:00",),
    ).lastrowid
    unrelated_task_id = conn.execute(
        "INSERT INTO task_runs (task_type, status, platform, started_at) "
        "VALUES ('comment_polling', 'pending_verify', 'xhs', ?)",
        ("2026-11-01 10:00:01",),
    ).lastrowid
    log_id = conn.execute(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status, task_run_id) "
        "VALUES ('xhs', 'active_comment', 'exact link', ?, 'pending_verify', ?)",
        ("2026-11-01 10:00:02", task_id),
    ).lastrowid
    conn.commit()

    response = tc.post(f"/api/ops_logs/{log_id}/resolve", json={"action": "sent"})
    assert response.status_code == 200
    assert response.json()["linked_task_run_id"] == task_id
    assert conn.execute("SELECT status FROM task_runs WHERE id = ?", (task_id,)).fetchone()[0] == "success"
    assert conn.execute("SELECT status FROM task_runs WHERE id = ?", (unrelated_task_id,)).fetchone()[0] == "pending_verify"


def test_resolve_pending_verify_does_not_update_unrelated_platform_logs(client):
    tc, conn, _ = client
    conn.execute(
        "INSERT INTO task_runs (task_type, status, platform, started_at) "
        "VALUES ('comment_polling', 'pending_verify', 'xhs', ?)",
        ("2026-10-28 07:00:00",),
    )
    run_id = conn.execute("SELECT MAX(id) FROM task_runs").fetchone()[0]
    conn.execute(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status, task_run_id) "
        "VALUES ('xhs', 'active_comment', '明确关联日志', ?, 'pending_verify', ?)",
        ("2026-10-28 07:01:00", run_id),
    )
    conn.execute(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
        "VALUES ('xhs', 'active_comment', '同平台但未关联日志', ?, 'pending_verify')",
        ("2026-10-28 07:01:01",),
    )
    conn.execute(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
        "VALUES ('douyin', 'active_comment', '其他平台日志', ?, 'pending_verify')",
        ("2026-10-28 07:01:00",),
    )
    conn.commit()

    response = tc.post(f"/api/task_runs/{run_id}/resolve", json={"action": "sent"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["ops_logs_sync"] == "linked_records_updated"
    assert body["ops_logs_updated"] == 1
    assert body["ops_logs_pending_verify"] == 0
    linked = conn.execute(
        "SELECT status, resolved_by FROM ops_logs WHERE task_run_id = ?", (run_id,)
    ).fetchone()
    assert linked["status"] == "success"
    assert linked["resolved_by"].startswith("session:")
    assert conn.execute(
        "SELECT COUNT(*) FROM ops_logs WHERE platform = 'xhs' AND status = 'pending_verify'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM ops_logs WHERE platform = 'douyin' AND status = 'pending_verify'"
    ).fetchone()[0] == 1


def test_draft_cancel(client):
    tc, conn, seed = client
    rid = seed.round("awaiting_publish")
    draft_id = seed.draft(rid, draft_status="draft_saved")

    assert tc.post("/api/drafts/nonexistent/cancel").status_code == 404
    r = tc.post(f"/api/drafts/{draft_id}/cancel")
    assert r.status_code == 200
    assert conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0] == "cancelled"


def test_scheduler_run_unknown_job_404(client):
    tc, _, _ = client

    class _StubSched:
        class scheduler:  # noqa: N801 - 模拟 APScheduler 属性名
            @staticmethod
            def get_job(_job_id: str):
                return None

    api_main._g_ctx["scheduler"] = _StubSched()
    assert tc.post("/api/scheduler/jobs/not_a_job/run").status_code == 404


# ---------------------------------------------------------------- Content plans

def test_content_plan_create_list_update_and_cancel_are_tenant_scoped(client):
    tc, conn, _ = client
    before_drafts = conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    payload = {
        "title": "本周产品内容计划",
        "topic": "围绕真实使用场景制作系列内容",
        "platforms": ["xhs", "douyin"],
        "planned_count": 3,
        "scheduled_date": "2026-11-02",
        "notes": "先做用户问题梳理",
    }
    created = tc.post("/api/content-plans", json=payload)
    assert created.status_code == 200
    plan = created.json()
    assert plan["status"] == "planned"
    assert plan["version"] == 1
    assert plan["platforms"] == ["douyin", "xhs"]

    listed = tc.get("/api/content-plans?start_date=2026-11-01&end_date=2026-11-03")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == plan["id"]

    updated = tc.patch(
        f"/api/content-plans/{plan['id']}",
        json={"version": 1, "scheduled_date": "2026-11-03", "topic": "补充主题说明"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["status"] == "planned"

    stale = tc.patch(
        f"/api/content-plans/{plan['id']}",
        json={"version": 1, "title": "过期窗口覆盖"},
    )
    assert stale.status_code == 409

    cancelled = tc.delete(f"/api/content-plans/{plan['id']}?version=2")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == before_drafts


def test_content_plan_rejects_invalid_payload_and_platform(client):
    tc, _, _ = client
    invalid_date = tc.post("/api/content-plans", json={
        "title": "计划", "scheduled_date": "2026-13-40",
    })
    assert invalid_date.status_code == 422

    invalid_count = tc.post("/api/content-plans", json={
        "title": "计划", "scheduled_date": "2026-11-02", "planned_count": 101,
    })
    assert invalid_count.status_code == 422

    invalid_platform = tc.post("/api/content-plans", json={
        "title": "计划", "scheduled_date": "2026-11-02", "platforms": ["not-a-platform"],
    })
    assert invalid_platform.status_code == 422



def test_content_plan_links_generated_drafts_idempotently_and_rolls_back_invalid_batch(client):
    tc, conn, seed = client
    created = tc.post("/api/content-plans", json={
        "title": "多平台内容计划", "scheduled_date": "2026-11-04",
        "platforms": ["xhs"], "planned_count": 2,
    })
    assert created.status_code == 200
    plan_id = created.json()["id"]
    round_id = seed.round()
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_saved")

    missing = tc.post(f"/api/content-plans/{plan_id}/drafts", json={
        "draft_ids": [draft_id, "missing-draft"],
    })
    assert missing.status_code == 404
    assert conn.execute(
        "SELECT COUNT(*) FROM content_plan_drafts WHERE plan_id=?", (plan_id,)
    ).fetchone()[0] == 0

    linked = tc.post(f"/api/content-plans/{plan_id}/drafts", json={"draft_ids": [draft_id]})
    assert linked.status_code == 200
    assert linked.json()["linked_count"] == 1
    duplicate = tc.post(f"/api/content-plans/{plan_id}/drafts", json={"draft_ids": [draft_id]})
    assert duplicate.status_code == 200
    assert duplicate.json()["linked_count"] == 0

    detail = tc.get(f"/api/content-plans/{plan_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "generated"
    assert detail.json()["drafts"][0]["draft_id"] == draft_id
    assert detail.json()["drafts"][0]["round_id"] == round_id


# ---------------------------------------------------------------- Safe tenant Skills registry

def test_skill_registry_registers_metadata_disabled_and_never_executes(client):
    tc, conn, _ = client
    created = tc.post("/api/skills", json={
        "skill_id": "my_content_audit",
        "name": "内容审阅辅助",
        "version": "1.0.0",
        "description": "登记一个只读内容审阅能力的 manifest",
        "category": "review",
        "inputs": [{"name": "content_id", "type": "string", "required": True}],
        "outputs": [{"name": "findings", "type": "array"}],
        "permissions": ["read_content"],
    })
    assert created.status_code == 200
    assert created.json()["enabled"] is False
    assert created.json()["execution_enabled"] is False

    listed = tc.get("/api/skills")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["execution_enabled"] is False

    enabled = tc.patch("/api/skills/my_content_audit", json={
        "enabled": True, "manifest_version": 1,
    })
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["execution_enabled"] is False

    blocked_delete = tc.delete("/api/skills/my_content_audit")
    assert blocked_delete.status_code == 409

    stale = tc.patch("/api/skills/my_content_audit", json={
        "enabled": False, "manifest_version": 1,
    })
    assert stale.status_code == 409

    edited = tc.patch("/api/skills/my_content_audit", json={
        "name": "内容审阅辅助 v2",
        "version": "1.1.0",
        "description": "更新后的只读 manifest",
        "inputs": [{"name": "content_id", "type": "string"}],
        "manifest_version": 2,
    })
    assert edited.status_code == 200
    assert edited.json()["name"] == "内容审阅辅助 v2"
    assert edited.json()["version"] == "1.1.0"
    assert edited.json()["manifest_version"] == 3
    assert edited.json()["enabled"] is True
    assert edited.json()["execution_enabled"] is False

    disabled = tc.patch("/api/skills/my_content_audit", json={
        "enabled": False, "manifest_version": 3,
    })
    assert disabled.status_code == 200
    deleted = tc.delete("/api/skills/my_content_audit")
    assert deleted.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM tenant_skill_registry WHERE skill_id='my_content_audit'"
    ).fetchone()[0] == 0


def test_skill_registry_rejects_unsafe_permissions_and_invalid_manifest(client):
    tc, _, _ = client
    unsafe = tc.post("/api/skills", json={
        "skill_id": "unsafe_skill", "name": "危险 Skill", "version": "1.0.0",
        "permissions": ["execute_python"],
    })
    assert unsafe.status_code == 422
    malformed_permissions = tc.post("/api/skills", json={
        "skill_id": "malformed_permissions", "name": "错误权限", "version": "1.0.0",
        "permissions": [{}],
    })
    assert malformed_permissions.status_code == 422

    invalid_version = tc.post("/api/skills", json={
        "skill_id": "bad_version", "name": "错误版本", "version": "latest",
    })
    assert invalid_version.status_code == 422

    reserved_id = tc.post("/api/skills", json={
        "skill_id": "topic_scoring", "name": "伪装内置能力", "version": "1.0.0",
    })
    assert reserved_id.status_code == 409


def test_content_plan_start_enqueues_global_task_and_cancel_only_cancels_queued_task(client, tmp_path, monkeypatch):
    tc, conn, _ = client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *args, **kwargs: {"test_snapshot": True})

    created = tc.post("/api/content-plans", json={
        "title": "排队生产计划", "scheduled_date": "2026-11-06",
        "platforms": ["xhs"], "planned_count": 2,
    })
    assert created.status_code == 200
    plan_id = created.json()["id"]

    started = tc.post(f"/api/content-plans/{plan_id}/start")
    assert started.status_code == 202
    body = started.json()
    assert body["round_id"] > 0
    assert body["queued"] is True
    queued_task = queue.get(body["task_id"])
    assert queued_task is not None and queued_task.status == "queued"

    detail = tc.get(f"/api/content-plans/{plan_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "generating"
    assert detail.json()["rounds"][0]["round_id"] == body["round_id"]
    assert detail.json()["rounds"][0]["task_id"] == body["task_id"]

    cancelled = tc.delete(f"/api/content-plans/{plan_id}?version=2")
    assert cancelled.status_code == 200
    assert queue.get(body["task_id"]).status == "cancelled"
    assert conn.execute(
        "SELECT status FROM content_plans WHERE id=?", (plan_id,)
    ).fetchone()["status"] == "cancelled"


def test_worker_result_links_round_drafts_and_updates_plan_status(client):
    tc, conn, seed = client
    created = tc.post("/api/content-plans", json={
        "title": "Worker 回写计划", "scheduled_date": "2026-11-08",
        "platforms": ["xhs"], "planned_count": 1,
    })
    assert created.status_code == 200
    plan_id = created.json()["id"]
    round_id = seed.round(status="producing")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_saved")
    conn.execute(
        "INSERT INTO content_plan_rounds(plan_id,round_id,task_id,created_at) VALUES(?,?,?,?)",
        (plan_id, round_id, "task-test", "2026-11-08T10:00:00"),
    )
    conn.execute(
        "UPDATE content_plans SET status='generating',version=version+1 WHERE id=?",
        (plan_id,),
    )
    conn.commit()

    GlobalTaskWorker._complete_content_plan(conn, round_id, {"final_status": "completed"})
    plan = conn.execute("SELECT status,version FROM content_plans WHERE id=?", (plan_id,)).fetchone()
    assert plan["status"] == "generated"
    assert plan["version"] == 3
    assert conn.execute(
        "SELECT round_id FROM content_plan_drafts WHERE plan_id=? AND draft_id=?",
        (plan_id, draft_id),
    ).fetchone()["round_id"] == round_id


def test_content_plan_recovers_to_retryable_after_worker_interruption(client, tmp_path, monkeypatch):
    tc, _, seed = client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    monkeypatch.setattr(api_main, "_build_task_execution_snapshot", lambda *args, **kwargs: {"test_snapshot": True})
    created = tc.post("/api/content-plans", json={
        "title": "中断恢复计划", "scheduled_date": "2026-11-09",
        "platforms": ["xhs"], "planned_count": 1,
    })
    plan_id = created.json()["id"]
    started = tc.post(f"/api/content-plans/{plan_id}/start")
    assert started.status_code == 202
    task_id = started.json()["task_id"]
    round_id = started.json()["round_id"]
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_saved")

    queue_conn = queue._connect()
    queue_conn.execute(
        "UPDATE global_task_queue SET status='interrupted',finished_at='2026-11-09T10:00:00' WHERE task_id=?",
        (task_id,),
    )
    queue_conn.commit()
    queue_conn.close()

    listed = tc.get("/api/content-plans?start_date=2026-11-09&end_date=2026-11-09")
    assert listed.status_code == 200
    recovered = listed.json()["items"][0]
    assert recovered["status"] == "failed"
    assert recovered["version"] == 3
    detail = tc.get(f"/api/content-plans/{plan_id}")
    assert detail.status_code == 200
    assert detail.json()["drafts"][0]["draft_id"] == draft_id


def test_orchestration_rejects_unregistered_disabled_and_unsupported_custom_skill_refs(client):
    tc, _, _ = client
    from src.core.orchestration import default_steps

    steps = default_steps()
    steps[0]["skill_id"] = "not_registered"
    unknown = tc.post("/api/orchestration/validate", json={"steps": steps})
    assert unknown.status_code == 200
    assert unknown.json()["valid"] is False
    assert any("未登记" in error for error in unknown.json()["errors"])

    registered = tc.post("/api/skills", json={
        "skill_id": "custom_review_helper", "name": "自定义审阅", "version": "1.0.0",
    })
    assert registered.status_code == 200
    steps[0]["skill_id"] = "custom_review_helper"
    disabled = tc.post("/api/orchestration/validate", json={"steps": steps})
    assert any("尚未启用" in error for error in disabled.json()["errors"])

    enabled = tc.patch("/api/skills/custom_review_helper", json={
        "enabled": True, "manifest_version": 1,
    })
    assert enabled.status_code == 200
    unsupported = tc.post("/api/orchestration/validate", json={"steps": steps})
    assert unsupported.json()["valid"] is False
    assert any("仅支持内置步骤" in error for error in unsupported.json()["errors"])

    saved = tc.put("/api/orchestration", json={"steps": steps})
    assert saved.status_code == 422


# ---------------------------------------------------------------- 图片素材库分页与统计
def test_media_assets_pagination_and_counts_are_database_backed(client):
    tc, conn, _ = client
    rows = []
    review_states = ("pending_review", "approved", "rejected")
    for index in range(45):
        rows.append((
            f"分页素材 {index:02d}",
            f"asset-{index:02d}.png",
            f"stored-{index:02d}.png",
            "image/png",
            1024,
            f"sha-{index:02d}",
            100,
            100,
            "archived" if index >= 40 else "active",
            review_states[index % 3],
        ))
    conn.executemany(
        "INSERT INTO media_assets "
        "(title, original_filename, stored_filename, mime_type, byte_size, sha256, width, height, status, review_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    first = tc.get("/api/media?status=active&review_status=all&page=1&page_size=20")
    second = tc.get("/api/media?status=active&review_status=all&page=2&page_size=20")
    assert first.status_code == second.status_code == 200
    first_body, second_body = first.json(), second.json()
    assert first_body["total"] == 40
    assert first_body["page"] == 1 and first_body["pages"] == 2
    assert len(first_body["items"]) == 20
    assert len(second_body["items"]) == 20
    assert {item["id"] for item in first_body["items"]}.isdisjoint(
        {item["id"] for item in second_body["items"]}
    )
    assert first_body["counts"] == {
        "all": 40, "pending": 14, "approved": 13, "rejected": 13, "archived": 5,
    }

    pending = tc.get("/api/media?status=active&review_status=pending_review&page=1&page_size=20")
    assert pending.status_code == 200
    assert pending.json()["total"] == 14
    assert all(item["review_status"] == "pending_review" for item in pending.json()["items"])


def test_media_assets_search_counts_are_not_limited_to_current_page(client):
    tc, conn, _ = client
    for index in range(25):
        conn.execute(
            "INSERT INTO media_assets "
            "(title, original_filename, stored_filename, mime_type, byte_size, sha256, width, height, status, review_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'approved')",
            (f"目标关键词 {index}", f"target-{index}.png", f"target-{index}.png",
             "image/png", 512, f"target-sha-{index}", 80, 80),
        )
    conn.commit()

    response = tc.get("/api/media?query=目标关键词&status=active&page=1&page_size=10")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 25
    assert body["pages"] == 3
    assert body["counts"]["all"] == 25
    assert body["counts"]["approved"] == 25


def test_media_asset_lifecycle_rename_restore_and_reference_safe_delete(client, tmp_path, seed):
    import json
    from types import SimpleNamespace

    tc, conn, _ = client
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    stored_name = "lifecycle-test.png"
    media_file = media_dir / stored_name
    media_file.write_bytes(b"test-image")

    class Registry:
        def paths(self, _tenant_id, create=False):
            return SimpleNamespace(media_dir=media_dir)

    api_main._g_ctx["tenant_registry"] = Registry()
    cur = conn.execute(
        "INSERT INTO media_assets "
        "(title, original_filename, stored_filename, mime_type, byte_size, sha256, width, height, status, review_status) "
        "VALUES ('旧名称', ?, ?, 'image/png', 10, 'lifecycle-sha', 10, 10, 'active', 'approved')",
        (stored_name, stored_name),
    )
    asset_id = int(cur.lastrowid)
    conn.commit()

    renamed = tc.patch(f"/api/media/{asset_id}", json={"title": "新名称"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "新名称"

    archived = tc.post(f"/api/media/{asset_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    restored = tc.post(f"/api/media/{asset_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["restored"] is True

    tc.post(f"/api/media/{asset_id}/archive")
    conn.execute(
        "INSERT INTO media_asset_usages (media_asset_id, usage_role) VALUES (?, 'cover')",
        (asset_id,),
    )
    conn.commit()
    blocked = tc.delete(f"/api/media/{asset_id}")
    assert blocked.status_code == 409
    assert media_file.exists()

    conn.execute("DELETE FROM media_asset_usages WHERE media_asset_id = ?", (asset_id,))
    # 兼容历史数据：即使未写 media_asset_usages，也必须检查 contents.image_paths。
    round_id = seed.round()
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_saved", fetch_status=None)
    conn.execute(
        "UPDATE contents SET image_paths = ? WHERE id = "
        "(SELECT content_id FROM drafts WHERE id = ?)",
        (json.dumps([str(media_file)]), draft_id),
    )
    conn.commit()
    legacy_ref_blocked = tc.delete(f"/api/media/{asset_id}")
    assert legacy_ref_blocked.status_code == 409
    assert "图片路径引用 1 条" in legacy_ref_blocked.json()["detail"]
    assert media_file.exists()

    conn.execute(
        "UPDATE contents SET image_paths = '[]' WHERE id = "
        "(SELECT content_id FROM drafts WHERE id = ?)",
        (draft_id,),
    )
    conn.commit()
    deleted = tc.delete(f"/api/media/{asset_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["file_removed"] is True
    assert not media_file.exists()


# ---------------------------------------------------------------- 运营日志 / 任务追踪 / 通知历史分页
def test_operations_history_endpoints_return_database_totals_and_pages(client):
    tc, conn, _ = client
    for index in range(45):
        stamp = f"2026-11-01 10:{index // 60:02d}:{index % 60:02d}"
        conn.execute(
            "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
            "VALUES ('xhs', 'passive_reply', ?, ?, 'success')",
            (f"日志 {index}", stamp),
        )
        conn.execute(
            "INSERT INTO task_runs (task_type, status, attempt, max_attempts, created_at) "
            "VALUES ('data_fetch', 'success', 1, 3, ?)",
            (stamp,),
        )
        conn.execute(
            "INSERT INTO notifications (level, type, title, channel, sent_at) "
            "VALUES ('info', 'round_completed', ?, 'macos', ?)",
            (f"通知 {index}", stamp),
        )
    conn.commit()

    endpoints = ("/api/ops_logs", "/api/task_runs", "/api/notifications")
    for endpoint in endpoints:
        first = tc.get(f"{endpoint}?page=1&page_size=20")
        second = tc.get(f"{endpoint}?page=2&page_size=20")
        assert first.status_code == second.status_code == 200
        first_body, second_body = first.json(), second.json()
        assert first_body["total"] == 45
        assert first_body["page"] == 1 and first_body["pages"] == 3
        assert len(first_body["items"]) == 20
        assert len(second_body["items"]) == 20
        assert {row["id"] for row in first_body["items"]}.isdisjoint(
            {row["id"] for row in second_body["items"]}
        )


def test_notifications_include_deterministic_deep_link_targets(client):
    tc, conn, _ = client
    rows = [
        ("login_expired", "登录态过期", "2026-11-01 11:00:01"),
        ("circuit_broken", "平台熔断", "2026-11-01 11:00:02"),
        ("draft_failed", "草稿保存失败", "2026-11-01 11:00:03"),
        ("round_completed", "轮次完成", "2026-11-01 11:00:04"),
    ]
    conn.executemany(
        "INSERT INTO notifications (level, type, title, sent_at) VALUES ('warning', ?, ?, ?)",
        rows,
    )
    conn.commit()

    response = tc.get("/api/notifications?page=1&page_size=10")
    assert response.status_code == 200
    targets = {item["type"]: (item["target_page"], item["target_tab"]) for item in response.json()["items"]}
    assert targets["login_expired"] == ("accounts", None)
    assert targets["circuit_broken"] == ("operations", "breaker")
    assert targets["draft_failed"] == ("calendar", None)
    assert targets["round_completed"] == ("operations", "logs")


def test_notifications_with_stable_references_deep_link_to_exact_record(client, seed):
    tc, conn, _ = client
    task = conn.execute(
        "INSERT INTO task_runs (task_type, biz_id, status, started_at) "
        "VALUES ('round_pipeline', 'scheduler:_job_round_review:[]', 'success', '2026-11-01 10:00:00')"
    )
    task_id = task.lastrowid
    log = conn.execute(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status, task_run_id) "
        "VALUES ('xhs', 'passive_reply', 'linked operation', '2026-11-01 10:00:01', 'success', ?)",
        (task_id,),
    )
    log_id = log.lastrowid
    round_id = seed.round(status="created")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="draft_saved", fetch_status=None)
    conn.execute(
        "INSERT INTO notifications (level, type, title, sent_at, task_run_id, ops_log_id) "
        "VALUES ('warning', 'round_timeout', 'linked task', '2026-11-01 10:00:02', ?, ?)",
        (task_id, log_id),
    )
    conn.execute(
        "INSERT INTO notifications (level, type, title, sent_at, task_run_id, round_id, draft_id) "
        "VALUES ('info', 'draft_saved', 'linked draft', '2026-11-01 10:00:03', ?, ?, ?)",
        (task_id, round_id, draft_id),
    )
    conn.execute(
        "INSERT INTO notifications (level, type, title, sent_at, round_id) "
        "VALUES ('info', 'round_completed', 'linked round', '2026-11-01 10:00:04', ?)",
        (round_id,),
    )
    conn.execute(
        "INSERT INTO notifications (level, type, title, sent_at, task_run_id) "
        "VALUES ('warning', 'login_expired', 'expired login', '2026-11-01 10:00:05', ?)",
        (task_id,),
    )
    conn.commit()

    response = tc.get("/api/notifications?page=1&page_size=10")
    assert response.status_code == 200
    items = {row["title"]: row for row in response.json()["items"]}
    item = items["linked task"]
    assert item["target_page"] == "operations"
    assert item["target_tab"] == "logs"
    assert item["target_type"] == "ops_log"
    assert item["target_id"] == log_id
    draft_item = items["linked draft"]
    assert draft_item["target_page"] == "calendar"
    assert draft_item["target_type"] == "draft"
    assert draft_item["target_id"] == draft_id
    round_item = items["linked round"]
    assert round_item["target_page"] == "operations"
    assert round_item["target_tab"] == "tasks"
    assert round_item["target_type"] == "round"
    assert round_item["target_id"] == round_id
    expired_item = items["expired login"]
    assert expired_item["target_page"] == "accounts"
    assert expired_item["target_tab"] is None
    assert expired_item["target_type"] is None

    exact_draft = tc.get(f"/api/drafts?draft_id={draft_id}")
    assert exact_draft.status_code == 200
    assert [row["id"] for row in exact_draft.json()] == [draft_id]
    exact_round_task = tc.get(f"/api/task_runs?round_id={round_id}&page=1&page_size=10")
    assert exact_round_task.status_code == 200
    assert exact_round_task.json()["items"][0]["id"] == task_id
    exact_task = tc.get(f"/api/task_runs?record_id={task_id}&page=1&page_size=10")
    exact_log = tc.get(f"/api/ops_logs?record_id={log_id}&page=1&page_size=10")
    assert [row["id"] for row in exact_task.json()["items"]] == [task_id]
    assert [row["id"] for row in exact_log.json()["items"]] == [log_id]


def test_notifications_read_state_filter_and_mark_read_are_idempotent(client):
    tc, conn, _ = client
    for index in range(3):
        conn.execute(
            "INSERT INTO notifications (level, type, title, channel, sent_at) "
            "VALUES ('info', 'round_completed', ?, 'macos', ?)",
            (f"已读测试 {index}", f"2026-11-01 11:00:0{index}"),
        )
    conn.commit()
    ids = [row[0] for row in conn.execute(
        "SELECT id FROM notifications ORDER BY sent_at, id"
    ).fetchall()]

    unread = tc.get("/api/notifications?page=1&page_size=10&read_status=unread")
    assert unread.status_code == 200
    assert unread.json()["total"] == 3
    assert unread.json()["unread_total"] == 3

    first = tc.post(f"/api/notifications/{ids[0]}/read")
    assert first.status_code == 200
    first_read_at = first.json()["read_at"]
    again = tc.post(f"/api/notifications/{ids[0]}/read")
    assert again.status_code == 200
    assert again.json()["read_at"] == first_read_at

    unread_after = tc.get("/api/notifications?page=1&page_size=10&read_status=unread")
    read_after = tc.get("/api/notifications?page=1&page_size=10&read_status=read")
    assert unread_after.json()["total"] == 2
    assert unread_after.json()["unread_total"] == 2
    assert read_after.json()["total"] == 1

    all_read = tc.post("/api/notifications/read-all")
    assert all_read.status_code == 200
    assert all_read.json()["updated"] == 2
    assert tc.get("/api/notifications?page=1&page_size=10&read_status=unread").json()["total"] == 0
    assert tc.post("/api/notifications/999999/read").status_code == 404
    assert tc.get("/api/notifications?page=1&read_status=invalid").status_code == 400


def test_operations_and_task_history_pagination_stress_5000_records(client):
    tc, conn, _ = client
    conn.executemany(
        "INSERT INTO task_runs (task_type, platform, status, created_at, started_at, error_msg) "
        "VALUES ('comment_polling', 'xhs', 'failed', '2026-11-01 12:00:00', '2026-11-01 12:00:00', '压力测试失败')",
        [() for _ in range(5000)],
    )
    conn.executemany(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
        "VALUES ('xhs', 'active_comment', '压力测试日志', '2026-11-01 12:00:00', 'failed')",
        [() for _ in range(5000)],
    )
    conn.commit()

    for endpoint in ("/api/task_runs", "/api/ops_logs"):
        first = tc.get(endpoint, params={"page": 1, "page_size": 100, "status": "failed"})
        last = tc.get(endpoint, params={"page": 50, "page_size": 100, "status": "failed"})
        beyond = tc.get(endpoint, params={"page": 51, "page_size": 100, "status": "failed"})
        assert first.status_code == last.status_code == beyond.status_code == 200
        assert first.json()["total"] == last.json()["total"] == 5000
        assert first.json()["pages"] == last.json()["pages"] == 50
        assert len(first.json()["items"]) == len(last.json()["items"]) == 100
        assert beyond.json()["items"] == []

    filtered = tc.get("/api/task_runs", params={
        "page": 1, "page_size": 100, "platform": "xhs",
        "status": "failed", "date_from": "2026-11-01", "date_to": "2026-11-01",
    })
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 5000


def test_notifications_pagination_boundary_and_5000_record_stress(client):
    tc, conn, _ = client
    current = 0
    for target in (0, 1, 12, 50, 100, 500, 5000):
        if target > current:
            conn.executemany(
                "INSERT INTO notifications (level, type, title, channel, sent_at) "
                "VALUES ('info', 'round_completed', ?, 'macos', ?)",
                [(f"压力通知 {index}", f"2026-11-01 {12 + index // 3600:02d}:{(index // 60) % 60:02d}:{index % 60:02d}")
                 for index in range(current, target)],
            )
            conn.commit()
        response = tc.get("/api/notifications?page=1&page_size=20")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == target
        assert body["pages"] == max(1, (target + 19) // 20)
        assert len(body["items"]) == min(target, 20)
        current = target

    last = tc.get("/api/notifications?page=250&page_size=20")
    assert last.status_code == 200
    assert last.json()["total"] == 5000
    assert len(last.json()["items"]) == 20
    assert tc.get("/api/notifications?page=251&page_size=20").json()["items"] == []
    assert tc.get("/api/notifications?page=0&page_size=20").status_code == 422
    assert tc.get("/api/notifications?page=1&page_size=101").status_code == 422


def test_hot_source_single_fetch_persists_diagnostics_without_persisting_hot_topics(client, cfg, monkeypatch):
    tc, conn, _ = client
    import src.core.hot_topics as hot_topics

    cfg.hot_topics.sources = [{
        "name": "测试 JSON 源",
        "type": "json",
        "url": "https://example.test/feed.json?api_key=do-not-store-this",
        "platform": "test-feed",
        "enabled": False,
    }]
    monkeypatch.setattr(
        hot_topics, "_fetch_public_text",
        lambda *args, **kwargs: ('{"items":[{"title":"单源测试热点","url":"https://example.test/item"}]}', "application/json"),
    )
    before = conn.execute("SELECT COUNT(*) FROM hot_topics").fetchone()[0]

    response = tc.post("/api/hot_topics/sources/0/test")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "success"
    assert body["item_count"] == 1
    assert body["sample_titles"] == ["单源测试热点"]
    assert "未写入热点库" in body["message"]
    assert conn.execute("SELECT COUNT(*) FROM hot_topics").fetchone()[0] == before

    # 来源详情返回最近一次持久化结果，刷新页面后仍可恢复。
    listed = tc.get("/api/hot_topics/sources").json()["items"]
    assert listed[0]["last_test"]["status"] == "success"
    assert listed[0]["last_test"]["sample_titles"] == ["单源测试热点"]
    assert "do-not-store-this" not in str(listed)
    history = tc.get("/api/hot_topics/sources/history").json()["items"]
    assert len(history) == 1
    assert history[0]["status"] == "success"
    assert "do-not-store-this" not in str(history)
    stored = conn.execute("SELECT source_key, message FROM hot_source_test_history").fetchone()
    assert len(stored["source_key"]) == 64
    assert "do-not-store-this" not in str(dict(stored))


def test_hot_source_failed_attempt_is_persisted_with_redacted_diagnostic(client, cfg, monkeypatch):
    tc, conn, _ = client
    import src.core.hot_topics as hot_topics

    cfg.hot_topics.sources = [{
        "name": "失败源",
        "type": "rss",
        "url": "https://example.test/private-feed",
        "platform": "test-feed",
        "enabled": True,
    }]

    def fail_fetch(*args, **kwargs):
        raise RuntimeError("fetch failed api_key=secret-value")

    monkeypatch.setattr(hot_topics, "_fetch_public_text", fail_fetch)
    response = tc.post("/api/hot_topics/sources/0/test")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    listed = tc.get("/api/hot_topics/sources").json()["items"]
    assert listed[0]["last_test"]["status"] == "failed"
    assert "secret-value" not in str(listed[0]["last_test"])
    history = tc.get("/api/hot_topics/sources/history").json()["items"]
    assert history[0]["status"] == "failed"
    assert "secret-value" not in str(history[0])
    assert conn.execute("SELECT COUNT(*) FROM hot_source_test_history").fetchone()[0] == 1


def test_custom_industry_preset_can_be_edited_and_deleted_but_builtin_is_protected(client, conn):
    tc, _, _ = client
    from src.core.industry_presets_repository import seed_builtins

    seed_builtins(conn)
    payload = {
        "name": "测试行业模板",
        "description": "用于验证自定义模板生命周期",
        "positioning": "为目标用户提供可执行建议",
        "persona_keywords": ["测试", "实践"],
        "forbidden_directions": ["虚假承诺"],
        "extra_redlines": ["夸大效果"],
        "dimensions": ["问题", "证据", "结论"],
        "platform_notes": {"xhs": "标题简洁"},
    }
    created = tc.put("/api/presets/test-custom", json=payload)
    assert created.status_code == 200
    assert created.json()["is_builtin"] is False

    updated = tc.put("/api/presets/test-custom", json={**payload, "name": "更新后的模板"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "更新后的模板"
    assert updated.json()["version"] == 2

    deleted = tc.delete("/api/presets/test-custom")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert tc.delete("/api/presets/test-custom").status_code == 404

    builtin_key = tc.get("/api/presets").json()["items"][0]["key"]
    assert tc.delete(f"/api/presets/{builtin_key}").status_code == 409


def test_global_todos_aggregate_tenant_sources_and_paginate(client, cfg, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    tc, conn, _ = client
    db_file = Path(cfg.resolve_path(cfg.paths.db_file))

    class Registry:
        def list(self):
            return [{"id": "default", "name": "测试账号组"}]

        def paths(self, _tenant_id, name=None):
            return SimpleNamespace(
                db_file=db_file,
                data_dir=Path(cfg.resolve_path(cfg.paths.data_dir)),
            )

    monkeypatch.setitem(api_main._g_ctx, "tenant_registry", Registry())
    monkeypatch.setattr(api_main, "_task_queue", None)

    conn.execute(
        "INSERT INTO platform_accounts (platform, account_handle, normalized_handle, display_name, status, notes) "
        "VALUES ('xhs', 'todo-account', 'todo-account', '待登录账号', 'active', '')"
    )
    conn.execute(
        "INSERT INTO task_runs (task_type, status, platform, error_msg, started_at) "
        "VALUES ('comment_polling', 'pending_verify', 'xhs', '需要核实发布结果', '2026-11-01 12:00:00')"
    )
    conn.execute(
        "INSERT INTO ops_logs (platform, action_type, content, executed_at, status) "
        "VALUES ('xhs', 'active_comment', '评论操作待核实', '2026-11-01 12:01:00', 'pending_verify')"
    )
    conn.execute(
        "INSERT INTO media_assets "
        "(title, original_filename, stored_filename, mime_type, byte_size, sha256, width, height, status, review_status) "
        "VALUES ('待审核素材', 'todo.png', 'todo.png', 'image/png', 10, 'todo-sha', 10, 10, 'active', 'pending_review')"
    )
    conn.execute(
        "INSERT INTO notifications (level, type, title, channel, sent_at) "
        "VALUES ('warning', 'round_completed', '待读通知', 'macos', '2026-11-01 12:02:00')"
    )
    conn.execute(
        "INSERT INTO media_generation_jobs (prompt, final_prompt, model, status, error_message) "
        "VALUES ('prompt', 'final prompt', 'test-model', 'failed', '配图生成失败')"
    )
    conn.execute(
        "INSERT INTO circuit_breaker_events (platform, reason, cooldown_until) "
        "VALUES ('xhs', 'login_expired', '2026-11-01 13:00:00')"
    )
    conn.execute(
        "INSERT INTO hot_topics (id, source, discovered_at, evidence_status, title) "
        "VALUES ('hot-failed-test', '60s/xhs', '2026-11-01 12:03:00', 'failed', '热点源抓取超时')"
    )
    conn.commit()

    response = tc.get("/api/todos?page=1&page_size=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 8
    assert body["pages"] == 4
    assert len(body["items"]) == 2
    assert body["counts"]["task_run"] == 1
    assert body["counts"]["ops_log"] == 1
    assert body["counts"]["circuit_breaker"] == 1
    assert body["counts"]["hot_topic_source"] == 1
    assert body["counts"]["media_review"] == 1
    assert body["counts"]["notification"] == 1
    assert body["counts"]["media_generation"] == 1
    assert body["counts"]["account_login"] == 1

    account_login_only = tc.get("/api/todos?page=1&page_size=10&source=account_login")
    assert account_login_only.status_code == 200
    assert account_login_only.json()["total"] == 1
    login_todo = account_login_only.json()["items"][0]
    assert login_todo["status"] == "missing"
    assert login_todo["target_page"] == "accounts"
    assert "不代表已有登录态已失效" in login_todo["detail"]

    hot_source_only = tc.get("/api/todos?page=1&page_size=10&source=hot_topic_source")
    assert hot_source_only.status_code == 200
    assert hot_source_only.json()["total"] == 1
    assert hot_source_only.json()["items"][0]["title"] == "热点来源采集失败 · 60s/xhs"

    breaker_only = tc.get("/api/todos?page=1&page_size=10&source=circuit_breaker")
    assert breaker_only.status_code == 200
    assert breaker_only.json()["total"] == 1
    expired_todo = breaker_only.json()["items"][0]
    assert expired_todo["target_page"] == "accounts"
    assert expired_todo["target_tab"] == "accounts"
    assert expired_todo["status"] == "login_expired"
    assert "实际鉴权失败" in expired_todo["detail"]

    media_only = tc.get("/api/todos?page=1&page_size=10&source=media_review")
    assert media_only.status_code == 200
    assert media_only.json()["total"] == 1
    assert media_only.json()["items"][0]["title"] == "素材待审核 · 待审核素材"


# ---------------------------------------------------------------- 配置审计历史分页
def test_config_audit_endpoint_paginates_all_redacted_changes(client):
    tc, conn, _ = client
    for index in range(47):
        conn.execute(
            "INSERT INTO config_changes (key, old_value, new_value, changed_at) "
            "VALUES (?, ?, ?, ?)",
            (f"rhythm.daily_quotas.xhs.passive_replies.{index}",
             f"old-{index}", f"new-{index}", f"2026-11-01 11:{index // 60:02d}:{index % 60:02d}"),
        )
    conn.commit()

    first = tc.get("/api/config/audit?page=1&page_size=20")
    second = tc.get("/api/config/audit?page=2&page_size=20")
    third = tc.get("/api/config/audit?page=3&page_size=20")
    assert first.status_code == second.status_code == third.status_code == 200
    a, b, c = first.json(), second.json(), third.json()
    assert a["total"] == b["total"] == c["total"] == 47
    assert a["pages"] == 3 and c["page"] == 3
    assert [len(a["items"]), len(b["items"]), len(c["items"])] == [20, 20, 7]
    ids = [row["id"] for body in (a, b, c) for row in body["items"]]
    assert len(ids) == len(set(ids)) == 47
    assert a["items"][0]["new_value"] == "new-46"


def test_config_audit_endpoint_rejects_invalid_pagination(client):
    tc, _, _ = client
    assert tc.get("/api/config/audit?page=0").status_code == 422
    assert tc.get("/api/config/audit?page=1&page_size=101").status_code == 422


# ---------------------------------------------------------------- 热点雷达数据库分页
def test_hot_topics_database_pagination_and_server_filters(client, cfg):
    tc, conn, _ = client
    cfg.hot_topics.filters["heat_filter_enabled"] = False
    cfg.hot_topics.filters["time_filter_enabled"] = False
    for index in range(45):
        conn.execute(
            "INSERT INTO hot_topics "
            "(id, source, evidence_status, title, heat_score, time_sensitive, last_seen_at) "
            "VALUES (?, ?, 'collected', ?, ?, 1, ?)",
            (f"paging-hot-{index:03d}", "test/xhs", f"分页热点-{index}",
             float(index + 1), "2026-11-01 10:00:00"),
        )
    conn.commit()

    first = tc.get("/api/hot_topics?page=1&page_size=20")
    second = tc.get("/api/hot_topics?page=2&page_size=20")
    third = tc.get("/api/hot_topics?page=3&page_size=20")
    assert first.status_code == second.status_code == third.status_code == 200
    a, b, c = first.json(), second.json(), third.json()
    assert [a["total"], b["total"], c["total"]] == [45, 45, 45]
    assert [len(a["items"]), len(b["items"]), len(c["items"])] == [20, 20, 5]
    ids = [item["id"] for body in (a, b, c) for item in body["items"]]
    assert len(ids) == len(set(ids)) == 45
    assert a["items"][0]["heat"] == 45
    assert a["urgent_total"] == b["urgent_total"] == c["urgent_total"] == 45
    assert a["match"] == b["match"] == c["match"]
    assert "test/xhs" in a["source_options"]

    filtered = tc.get("/api/hot_topics?page=1&page_size=20&source=test%2Fxhs&keyword=热点-1")
    assert filtered.status_code == 200
    result = filtered.json()
    assert result["total"] == 11  # "热点-1" also matches 10–19
    assert all("热点-1" in item["title"] for item in result["items"])


def test_hot_topics_database_pagination_preserves_latest_batch_and_failure_suppression(client, cfg):
    tc, conn, _ = client
    cfg.hot_topics.filters["heat_filter_enabled"] = False
    cfg.hot_topics.filters["time_filter_enabled"] = False
    conn.execute(
        "INSERT INTO hot_topics (id, source, evidence_status, title, heat_score, time_sensitive, last_seen_at) "
        "VALUES ('old-batch', 'batch/xhs', 'collected', '旧批次', 999, 1, '2026-10-30 10:00:00')"
    )
    conn.execute(
        "INSERT INTO hot_topics (id, source, evidence_status, title, heat_score, time_sensitive, last_seen_at) "
        "VALUES ('new-batch', 'batch/xhs', 'collected', '最新批次', 20, 1, '2026-11-01 10:00:00')"
    )
    conn.execute(
        "INSERT INTO hot_topics (id, source, evidence_status, title, discovered_at) "
        "VALUES ('failed-source', 'broken/douyin', 'failed', '采集失败', '2026-11-01 10:01:00')"
    )
    conn.execute(
        "INSERT INTO hot_topics (id, source, evidence_status, title, heat_score, last_seen_at) "
        "VALUES ('old-failed-platform', 'broken/douyin', 'collected', '失败平台旧数据', 88, '2026-11-01 09:00:00')"
    )
    conn.commit()

    response = tc.get("/api/hot_topics?page=1&page_size=20")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert "old-batch" not in ids
    assert "new-batch" in ids
    assert "old-failed-platform" not in ids
    assert any(source["status"] == "failed" for source in response.json()["sources"])


# ---------------------------------------------------------------- 逐篇指标数据库分页
def test_metrics_endpoint_database_pagination_and_filtered_total(client):
    tc, conn, _ = client
    for index in range(45):
        conn.execute(
            "INSERT INTO metrics (platform, fetch_status, fetched_at, views, likes, interaction_rate) "
            "VALUES ('xhs', 'success', ?, ?, ?, ?)",
            (f"2026-11-01 10:{index // 60:02d}:{index % 60:02d}",
             100 + index, 10 + index, (index + 1) / 1000),
        )
    conn.commit()

    first = tc.get("/api/metrics?page=1&page_size=20&fetch_status=success")
    second = tc.get("/api/metrics?page=2&page_size=20&fetch_status=success")
    third = tc.get("/api/metrics?page=3&page_size=20&fetch_status=success")
    assert first.status_code == second.status_code == third.status_code == 200
    a, b, c = first.json(), second.json(), third.json()
    assert [a["total"], b["total"], c["total"]] == [45, 45, 45]
    assert [len(a["items"]), len(b["items"]), len(c["items"])] == [20, 20, 5]
    ids = [item["id"] for body in (a, b, c) for item in body["items"]]
    assert len(ids) == len(set(ids)) == 45
    assert a["pages"] == 3 and c["page"] == 3
    assert a["z_stats"] == b["z_stats"] == c["z_stats"]

    filtered = tc.get("/api/metrics?page=1&page_size=20&platform=xhs&fetch_status=failed")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0
    assert filtered.json()["items"] == []
    status_only = tc.get("/api/metrics?page=1&page_size=20&fetch_status=failed")
    assert status_only.status_code == 200
    assert status_only.json()["total"] == 0
    legacy = tc.get("/api/metrics?limit=1")
    assert legacy.status_code == 200
    assert len(legacy.json()["items"]) == 1


def test_global_task_retry_api_requires_platform_confirmation_and_preserves_snapshot(
    client, tmp_path, monkeypatch,
):
    tc, _, _ = client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    original, _ = queue.enqueue(
        task_type="round_run",
        tenant_id="tenant-a",
        aggregate_id="round-42",
        payload={"round_id": 42, "execution_snapshot": {"account_id": "account-a", "platform": "xhs"}},
    )
    claimed = queue.claim_next("worker-test")
    assert claimed is not None and claimed.task_id == original.task_id
    assert queue.fail(
        original.task_id, "worker-test",
        error_code="network_timeout", error_message="平台结果待核实",
    )

    denied = tc.post(f"/api/task-queue/{original.task_id}/retry", json={
        "confirmed_not_executed": False,
        "confirmation_note": "尚未确认",
    })
    assert denied.status_code == 400
    assert queue.list_page(scope="active")[1] == 0

    response = tc.post(f"/api/task-queue/{original.task_id}/retry", json={
        "confirmed_not_executed": True,
        "confirmation_note": "已在平台后台确认没有对应内容记录",
    })
    assert response.status_code == 200
    result = response.json()
    assert result["created"] is True
    assert result["retry_of_task_id"] == original.task_id
    retry = queue.get(result["task_id"])
    assert retry is not None
    assert retry.tenant_id == original.tenant_id
    assert retry.payload == original.payload
    assert retry.status == "queued"

    repeated = tc.post(f"/api/task-queue/{original.task_id}/retry", json={
        "confirmed_not_executed": True,
        "confirmation_note": "重复提交不应生成第二条恢复任务",
    })
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["task_id"] == retry.task_id
    assert queue.list_page(scope="active")[1] == 1



def test_global_task_resolution_api_records_actor_and_keeps_execution_status_immutable(
    client, tmp_path, monkeypatch,
):
    tc, _, _ = client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    monkeypatch.setattr(api_main, "_task_queue", queue)
    original, _ = queue.enqueue(
        task_type="scheduler_job", tenant_id="tenant-a",
        payload={"job": "daily_ops"},
    )
    claimed = queue.claim_next("worker-test")
    assert claimed is not None and claimed.task_id == original.task_id
    assert queue.fail(
        original.task_id, "worker-test",
        error_code="job_failed", error_message="每日运营任务部分失败",
    )

    invalid = tc.post(f"/api/task-queue/{original.task_id}/resolve", json={
        "action": "handled", "note": "短",
    })
    assert invalid.status_code == 400

    response = tc.post(f"/api/task-queue/{original.task_id}/resolve", json={
        "action": "handled", "note": "已检查失败项并完成必要的人工补救",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["action"] == "handled"
    assert body["actor"]
    assert body["acted_at"]

    task = queue.get(original.task_id)
    assert task is not None
    assert task.status == "failed"
    assert task.resolution_action == "handled"
    assert task.resolution_actor == body["actor"]

    repeated = tc.post(f"/api/task-queue/{original.task_id}/resolve", json={
        "action": "handled", "note": "重复提交不能覆盖原始审计说明",
    })
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["note"] == "已检查失败项并完成必要的人工补救"

    conflict = tc.post(f"/api/task-queue/{original.task_id}/resolve", json={
        "action": "ignored", "note": "不允许覆盖首次处理决定",
    })
    assert conflict.status_code == 409


def test_global_todos_exclude_queue_items_with_audited_resolution(
    client, cfg, tmp_path, monkeypatch,
):
    from pathlib import Path
    from types import SimpleNamespace

    tc, _, _ = client
    queue = GlobalTaskQueue(tmp_path / "shared" / "system.db")
    queue.initialize()
    tasks = []
    worker_id = "worker-todos"
    for index in range(2):
        task, _ = queue.enqueue(
            task_type="scheduler_job", tenant_id="default",
            payload={"job": f"job-{index}"},
        )
        claimed = queue.claim_next(worker_id)
        if claimed is None:
            diagnostic = queue._connect()
            try:
                queued_row = diagnostic.execute(
                    "SELECT task_id,status,available_at,created_at FROM global_task_queue WHERE task_id=?",
                    (task.task_id,),
                ).fetchone()
                lease_row = diagnostic.execute(
                    "SELECT owner,expires_at FROM global_task_worker_lease WHERE singleton_id=1"
                ).fetchone()
            finally:
                diagnostic.close()
            raise AssertionError(
                f"queue claim unexpectedly empty at index={index}; "
                f"task={dict(queued_row) if queued_row else None}; "
                f"lease={dict(lease_row) if lease_row else None}"
            )
        assert claimed.task_id == task.task_id
        assert queue.fail(
            task.task_id, worker_id,
            error_code="job_failed", error_message=f"失败任务 {index}",
        )
        tasks.append(task)

    queue.resolve_failed_task(
        tasks[0].task_id, action="handled", actor="operator",
        note="已经人工核查并完成处理",
    )

    db_file = Path(cfg.resolve_path(cfg.paths.db_file))

    class Registry:
        def list(self):
            return [{"id": "default", "name": "默认账号组"}]

        def paths(self, _tenant_id, name=None):
            return SimpleNamespace(db_file=db_file)

    monkeypatch.setitem(api_main._g_ctx, "tenant_registry", Registry())
    monkeypatch.setattr(api_main, "_task_queue", queue)

    response = tc.get("/api/todos?source=global_queue")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["record_id"] for item in body["items"]] == [tasks[1].task_id]

def test_media_assets_5000_row_pagination_pressure(client):
    tc, conn, _ = client
    conn.execute("DELETE FROM media_assets")
    conn.commit()
    conn.executemany(
        "INSERT INTO media_assets "
        "(title, original_filename, stored_filename, mime_type, byte_size, sha256, width, height, status, review_status) "
        "VALUES (?, ?, ?, 'image/png', 1024, ?, 100, 100, 'active', 'approved')",
        [
            (f"压力素材 {index:04d}", f"pressure-{index:04d}.png",
             f"pressure-{index:04d}.png", f"pressure-sha-{index:04d}")
            for index in range(5000)
        ],
    )
    conn.commit()

    first = tc.get("/api/media?status=active&review_status=all&page=1&page_size=100")
    last = tc.get("/api/media?status=active&review_status=all&page=50&page_size=100")
    beyond = tc.get("/api/media?status=active&review_status=all&page=51&page_size=100")
    assert first.status_code == last.status_code == beyond.status_code == 200
    first_body, last_body, beyond_body = first.json(), last.json(), beyond.json()
    assert first_body["total"] == last_body["total"] == 5000
    assert first_body["pages"] == last_body["pages"] == 50
    assert len(first_body["items"]) == len(last_body["items"]) == 100
    assert beyond_body["total"] == 5000
    assert beyond_body["items"] == []
    assert first_body["counts"]["all"] == 5000
    assert first_body["counts"]["approved"] == 5000
    first_ids = {item["id"] for item in first_body["items"]}
    last_ids = {item["id"] for item in last_body["items"]}
    assert first_ids.isdisjoint(last_ids)
    assert first_body["items"][0]["title"] == "压力素材 4999"
    assert last_body["items"][-1]["title"] == "压力素材 0000"


def test_content_assets_5000_row_pagination_pressure(client):
    tc, conn, _ = client
    conn.execute("DELETE FROM content_assets")
    conn.commit()
    conn.executemany(
        "INSERT INTO content_assets "
        "(asset_type, title, body, source_url, platform, created_at, updated_at) "
        "VALUES ('voice', ?, ?, NULL, 'xhs', ?, ?)",
        [
            (f"压力声音资产 {index:04d}", f"内容资产分页压力测试 {index}",
             "2026-11-01 12:00:00", "2026-11-01 12:00:00")
            for index in range(5000)
        ],
    )
    conn.commit()

    first = tc.get("/api/assets?asset_type=voice&page=1&page_size=100")
    last = tc.get("/api/assets?asset_type=voice&page=50&page_size=100")
    beyond = tc.get("/api/assets?asset_type=voice&page=51&page_size=100")
    assert first.status_code == last.status_code == beyond.status_code == 200
    first_body, last_body, beyond_body = first.json(), last.json(), beyond.json()
    assert first_body["total"] == last_body["total"] == 5000
    assert first_body["pages"] == last_body["pages"] == 50
    assert len(first_body["items"]) == len(last_body["items"]) == 100
    assert beyond_body["total"] == 5000
    assert beyond_body["items"] == []
    first_ids = {item["id"] for item in first_body["items"]}
    last_ids = {item["id"] for item in last_body["items"]}
    assert first_ids.isdisjoint(last_ids)
    assert first_body["items"][0]["title"] == "压力声音资产 4999"
    assert last_body["items"][-1]["title"] == "压力声音资产 0000"


def test_hot_topics_5000_row_pagination_pressure(client):
    tc, conn, _ = client
    conn.execute("DELETE FROM hot_topics")
    conn.commit()
    conn.executemany(
        "INSERT INTO hot_topics "
        "(id, source, discovered_at, evidence_status, title, heat_score, time_sensitive) "
        "VALUES (?, 'stress/pagination', '2026-11-01 12:00:00', 'collected', ?, 6000, 1)",
        [
            (f"hot-{index:05d}", f"热点分页压力 {index:05d}")
            for index in range(5000)
        ],
    )
    conn.commit()

    first = tc.get("/api/hot_topics?page=1&page_size=100&tab=all&sort_by=heat")
    last = tc.get("/api/hot_topics?page=50&page_size=100&tab=all&sort_by=heat")
    beyond = tc.get("/api/hot_topics?page=51&page_size=100&tab=all&sort_by=heat")
    assert first.status_code == last.status_code == beyond.status_code == 200
    first_body, last_body, beyond_body = first.json(), last.json(), beyond.json()
    assert first_body["total"] == last_body["total"] == 5000
    assert first_body["pages"] == last_body["pages"] == 50
    assert len(first_body["items"]) == len(last_body["items"]) == 100
    assert beyond_body["total"] == 5000
    assert beyond_body["items"] == []
    first_ids = {item["id"] for item in first_body["items"]}
    last_ids = {item["id"] for item in last_body["items"]}
    assert first_ids.isdisjoint(last_ids)
    assert first_body["items"][0]["id"] == "hot-04999"
    assert last_body["items"][-1]["id"] == "hot-00000"


def test_task_runs_and_ops_logs_5000_row_pagination_and_filter_pressure(client):
    tc, conn, _ = client
    conn.execute("DELETE FROM notifications")
    conn.execute("DELETE FROM ops_logs")
    conn.execute("DELETE FROM task_runs")
    now = "2026-11-01 10:00:00"
    task_rows = [
        ("daily_ops", f"stress-queue-{i:05d}", "failed" if i % 2 else "success",
         "xhs", 1, now, now, now)
        for i in range(5000)
    ]
    conn.executemany(
        "INSERT INTO task_runs (task_type, biz_id, status, platform, attempt, started_at, finished_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        task_rows,
    )
    task_ids = [row[0] for row in conn.execute("SELECT id FROM task_runs ORDER BY id").fetchall()]
    log_rows = [
        (task_ids[i], "xhs", "active_comment", f"stress-log-{i:05d}",
         "failed" if i % 2 else "success", now)
        for i in range(5000)
    ]
    conn.executemany(
        "INSERT INTO ops_logs (task_run_id, platform, action_type, content, status, executed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        log_rows,
    )
    conn.commit()

    for endpoint in ("/api/task_runs", "/api/ops_logs"):
        first = tc.get(endpoint, params={"page": 1, "page_size": 100})
        fiftieth = tc.get(endpoint, params={"page": 50, "page_size": 100})
        beyond = tc.get(endpoint, params={"page": 51, "page_size": 100})
        assert first.status_code == fiftieth.status_code == beyond.status_code == 200
        first_body, last_body = first.json(), fiftieth.json()
        assert first_body["total"] == 5000
        assert first_body["pages"] == 50
        assert len(first_body["items"]) == len(last_body["items"]) == 100
        assert beyond.json()["items"] == []
        assert len({item["id"] for item in first_body["items"] + last_body["items"]}) == 200
        assert first_body["items"][0]["id"] > first_body["items"][-1]["id"]

        filtered = tc.get(endpoint, params={"page": 1, "page_size": 100, "status": "failed"})
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 2500
        assert filtered.json()["pages"] == 25
        assert all(item["status"] == "failed" for item in filtered.json()["items"])


def test_platform_preflight_status_is_consistent_across_progress_and_accounts(client, cfg):
    tc, conn, _ = client
    from pathlib import Path
    cfg.platforms.kuaishou.enabled = False
    conn.execute(
        "INSERT INTO platform_accounts "
        "(platform, account_handle, normalized_handle, display_name, status, notes) "
        "VALUES ('xhs', 'test-xhs', 'test-xhs', '测试小红书账号', 'active', '')"
    )
    login_dir = Path(cfg.resolve_path(cfg.paths.data_dir)) / "login_states"
    login_dir.mkdir(parents=True, exist_ok=True)
    (login_dir / "xhs.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    conn.commit()

    progress = tc.get("/api/platform_progress")
    assert progress.status_code == 200
    progress_body = progress.json()
    accounts = tc.get("/api/accounts")
    assert accounts.status_code == 200
    account_rows = {item["platform"]: item for item in accounts.json()["accounts"]}

    xhs = progress_body["xhs"]
    assert xhs["supported"] is True
    assert xhs["configured"] is True
    assert xhs["enabled"] is True
    assert xhs["account_registered"] is True
    assert xhs["account_active"] is True
    assert xhs["credential_present"] is True
    assert xhs["session_validity"] == "not_checked"
    assert xhs["preflight_ready"] is True
    assert account_rows["xhs"]["preflight_ready"] is True
    assert account_rows["xhs"]["credential_present"] is True

    kuaishou = progress_body["kuaishou"]
    assert kuaishou["enabled"] is False
    assert "platform_disabled" in kuaishou["blockers"]
    assert kuaishou["preflight_ready"] is False

    douyin = progress_body["douyin"]
    assert douyin["account_registered"] is False
    assert "account_not_registered" in douyin["blockers"]
    assert douyin["preflight_ready"] is False



def test_tenant_backup_failure_does_not_expose_internal_paths(client, monkeypatch):
    tc, _conn, _seed = client

    def fail_backup(*_args, **_kwargs):
        raise OSError("/srv/iterwave/tenants/brand-a/backups: permission denied")

    monkeypatch.setattr("src.core.tenant_backup.create_backup", fail_backup)
    response = tc.post("/api/tenant-backup")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == "账号组备份失败，请检查备份目录权限或联系管理员"
    assert "/srv/iterwave" not in detail
    assert "brand-a" not in detail



def test_kb_source_preview_failure_hides_server_paths(client, monkeypatch):
    tc, _conn, _seed = client

    def fail_preview(*_args, **_kwargs):
        raise OSError("/srv/iterwave/private/tenant-a/topics.xlsx permission denied")

    monkeypatch.setattr("src.core.kb_sources.preview_source", fail_preview)
    response = tc.get("/api/kb/sources/test-source/preview")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == "选题库来源预览失败，请检查来源配置与访问权限"
    assert "/srv/iterwave" not in detail
    assert "tenant-a" not in detail



def test_content_template_versions_and_lifecycle_are_preserved(client):
    tc, conn, _ = client
    created = tc.post("/api/assets", json={
        "asset_type": "template",
        "title": "模板 v1",
        "body": "问题 -> 证据 -> 结论",
    })
    assert created.status_code == 200
    template = created.json()
    template_id = template["id"]
    assert template["version"] == 1
    assert template["is_active"] == 1

    updated = tc.put(f"/api/assets/{template_id}", json={
        "asset_type": "template",
        "title": "模板 v2",
        "body": "背景 -> 实测 -> 结论",
    })
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    versions = tc.get(f"/api/assets/{template_id}/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()["items"]] == [2, 1]
    assert versions.json()["items"][1]["body"] == "问题 -> 证据 -> 结论"

    archived = tc.post(f"/api/assets/{template_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["is_active"] is False
    assert tc.get("/api/assets?asset_type=template").json()["total"] == 0
    assert tc.get("/api/assets?asset_type=template&include_inactive=true").json()["total"] == 1

    restored = tc.post(f"/api/assets/{template_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert tc.get("/api/assets?asset_type=template").json()["total"] == 1
