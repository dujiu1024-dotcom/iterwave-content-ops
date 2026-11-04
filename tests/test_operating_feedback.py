from __future__ import annotations

import json
import uuid

import pytest

from src.core.operating_feedback import (
    OperatingFeedbackError,
    acknowledge_close_event,
    build_decision_context,
    create_collection_run,
    create_publish_verification,
    retry_review_task,
    save_review_conclusion,
)


def _published_draft(conn, seed, *, status="user_published"):
    round_id = seed.round()
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status=status, fetch_status=None,
    )
    now = "2026-11-04T02:00:00+00:00"
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=?,published_at=? WHERE id=?",
        (now, now, draft_id),
    )
    conn.commit()
    return draft_id, now


def _verification(conn, seed):
    draft_id, published_at = _published_draft(conn, seed)
    result = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id="note-123", content_url="https://www.xiaohongshu.com/explore/note-123",
        published_at=published_at, idempotency_key="publish:note-123",
    )
    conn.commit()
    return draft_id, result


def test_publish_verification_requires_real_user_confirmation(conn, seed):
    draft_id, _ = _published_draft(conn, seed, status="draft_saved")
    with pytest.raises(OperatingFeedbackError, match="必须先由用户明确确认"):
        create_publish_verification(
            conn, tenant_id="tenant-test", draft_id=draft_id,
            platform_content_id="note-123",
            content_url="https://www.xiaohongshu.com/explore/note-123",
            published_at="2026-11-04T02:00:00+00:00",
        )


def test_publish_verification_is_idempotent_and_creates_separate_review_and_outbox(conn, seed):
    draft_id, result = _verification(conn, seed)

    repeated = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id="note-123", content_url="https://www.xiaohongshu.com/explore/note-123",
        published_at="2026-11-04T02:00:00+00:00", idempotency_key="publish:note-123",
    )
    assert repeated["already_verified"] is True
    assert repeated["verification_id"] == result["verification_id"]
    assert conn.execute("SELECT COUNT(*) FROM review_tasks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM production_line_close_outbox").fetchone()[0] == 1
    event = conn.execute("SELECT * FROM production_line_close_outbox").fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["event_type"] == "content.publish_verified.v1"
    assert payload["platform_content_id"] == "note-123"
    assert payload["content_snapshot"]["content_id"] == result["content_id"]
    assert len(payload["content_snapshot_sha256"]) == 64

    with pytest.raises(OperatingFeedbackError, match="不能覆盖原始发布证据"):
        create_publish_verification(
            conn, tenant_id="tenant-test", draft_id=draft_id,
            platform_content_id="different-note",
            content_url="https://www.xiaohongshu.com/explore/different-note",
            published_at="2026-11-04T02:00:00+00:00", idempotency_key="publish:other",
        )


def test_close_outbox_ack_is_idempotent_and_failure_can_retry(conn, seed):
    _, result = _verification(conn, seed)
    event_id = conn.execute("SELECT event_id FROM production_line_close_outbox").fetchone()[0]

    failed = acknowledge_close_event(conn, event_id, succeeded=False, error="生产线仍在运行")
    assert failed["status"] == "failed"
    assert conn.execute(
        "SELECT closure_status FROM content_publish_verifications WHERE verification_id=?",
        (result["verification_id"],),
    ).fetchone()[0] == "failed"

    from src.core.operating_feedback import retry_close_event
    assert retry_close_event(conn, event_id)["status"] == "pending"
    assert acknowledge_close_event(conn, event_id, succeeded=True)["status"] == "delivered"
    assert acknowledge_close_event(conn, event_id, succeeded=True)["already_acknowledged"] is True
    assert conn.execute(
        "SELECT closure_status FROM content_publish_verifications WHERE verification_id=?",
        (result["verification_id"],),
    ).fetchone()[0] == "succeeded"


def test_failed_collection_is_not_zero_and_partial_zero_is_a_fact(conn, seed):
    _, verification = _verification(conn, seed)
    task_id = verification["review_task_id"]

    failed = create_collection_run(
        conn, task_id=task_id, source="platform_readback", status="failed",
        metrics={}, error_message="平台暂不可用", idempotency_key="collect-failed",
    )
    conn.commit()
    assert failed["status"] == "failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM metric_facts WHERE source_record_type='review_collection_run'"
    ).fetchone()[0] == 0

    retry_review_task(conn, task_id)
    partial = create_collection_run(
        conn, task_id=task_id, source="manual", status="partial",
        metrics={"views": 0, "likes": 7}, window_start="2026-11-04T02:00:00+00:00",
        window_end="2026-11-04T11:00:00+00:00", timezone_name="Asia/Shanghai",
        idempotency_key="collect-partial", measured_at="2026-11-04T11:00:00+00:00",
    )
    conn.commit()
    assert partial["status"] == "partial"
    rows = conn.execute(
        "SELECT metric_key,value,collection_status,timezone_name FROM metric_facts "
        "WHERE source_record_type='review_collection_run' ORDER BY metric_key"
    ).fetchall()
    assert [(r["metric_key"], r["value"]) for r in rows] == [("likes", 7.0), ("views", 0.0)]
    assert all(r["collection_status"] == "partial" for r in rows)
    assert all(r["timezone_name"] == "Asia/Shanghai" for r in rows)

    with pytest.raises(OperatingFeedbackError, match="真实零值"):
        create_collection_run(
            conn, task_id=task_id, source="manual", status="succeeded",
            metrics={}, idempotency_key="empty-success",
        )


def test_collection_idempotency_and_metric_fact_audit(conn, seed):
    _, verification = _verification(conn, seed)
    task_id = verification["review_task_id"]
    payload = dict(
        task_id=task_id, source="manual", status="succeeded",
        metrics={"views": 0}, idempotency_key="same-run",
    )
    first = create_collection_run(conn, **payload)
    repeated = create_collection_run(conn, **payload)
    conn.commit()
    assert repeated["already_recorded"] is True
    assert repeated["run_id"] == first["run_id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM metric_fact_audit WHERE fact_id IN "
        "(SELECT fact_id FROM metric_facts WHERE source_record_type='review_collection_run')"
    ).fetchone()[0] == 1

    with pytest.raises(OperatingFeedbackError, match="不同的采集请求"):
        create_collection_run(
            conn, task_id=task_id, source="manual", status="succeeded",
            metrics={"views": 1}, idempotency_key="same-run",
        )


def test_review_conclusion_updates_business_gaps_with_history(conn, seed):
    _, verification = _verification(conn, seed)
    task_id = verification["review_task_id"]
    create_collection_run(
        conn, task_id=task_id, source="manual", status="succeeded",
        metrics={"views": 10}, idempotency_key="conclusion-data",
    )
    first = save_review_conclusion(
        conn, task_id=task_id, conclusion="曝光低于阶段预期",
        evidence=[{"metric": "views", "value": 10}],
        target_impact={"awareness": "needs_attention"},
        recommendations=["调整选题角度"],
        gap_changes=[{
            "gap_key": "audience-awareness", "title": "用户认知不足",
            "description": "曝光和互动尚未达到目标", "severity": "high",
            "status": "active", "reason": "本次复盘证据",
            "evidence": {"views": 10},
        }],
    )
    conn.commit()
    gap = conn.execute("SELECT * FROM business_gaps WHERE gap_key='audience-awareness'").fetchone()
    assert gap["status"] == "active"
    assert conn.execute("SELECT COUNT(*) FROM business_gap_events WHERE gap_id=?", (gap["gap_id"],)).fetchone()[0] == 1

    save_review_conclusion(
        conn, task_id=task_id, conclusion="认知缺口已改善",
        evidence={"source": "follow-up"}, target_impact={},
        recommendations=[], gap_changes=[{
            "gap_key": "audience-awareness", "title": "用户认知不足",
            "severity": "low", "status": "resolved", "reason": "复盘确认已改善",
        }],
    )
    conn.commit()
    events = conn.execute(
        "SELECT event_type FROM business_gap_events WHERE gap_id=? ORDER BY event_id",
        (gap["gap_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events] == ["opened", "resolved"]
    context = build_decision_context(conn, tenant_id="tenant-test")
    assert context["active_business_gaps"] == []
    assert context["latest_review"]["task_id"] == task_id


def test_legacy_metric_sources_are_normalized_without_turning_null_into_zero(conn, seed):
    round_id = seed.round()
    draft_id = seed.draft(
        round_id, draft_status="published", fetch_status="success",
        views=0, likes=None, collects=3,
    )
    facts = conn.execute(
        "SELECT metric_key,value,tenant_id FROM metric_facts "
        "WHERE source_record_type='metrics' AND source_record_id=(SELECT CAST(id AS TEXT) FROM metrics WHERE draft_id=?) "
        "ORDER BY metric_key", (draft_id,),
    ).fetchall()
    values = {row["metric_key"]: row["value"] for row in facts}
    assert values["views"] == 0
    assert values["collects"] == 3
    assert "likes" not in values
    assert all(row["tenant_id"] == "legacy" for row in facts)

    content_id = conn.execute("SELECT content_id FROM drafts WHERE id=?", (draft_id,)).fetchone()[0]
    snapshot_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO metric_snapshots
           (id,tenant_id,draft_id,content_id,platform,source,views,likes,collects,
            comments,shares,measured_at,recorded_at,created_at)
           VALUES(?,?,?,?,?,'manual',0,NULL,NULL,NULL,NULL,?,?,?)""",
        (snapshot_id, "tenant-test", draft_id, content_id, "xhs",
         "2026-11-04T11:00:00+00:00", "2026-11-04T11:01:00+00:00",
         "2026-11-04T11:01:00+00:00"),
    )
    conn.commit()
    snapshot_facts = conn.execute(
        "SELECT metric_key,value FROM metric_facts WHERE source_record_type='metric_snapshot' AND source_record_id=?",
        (snapshot_id,),
    ).fetchall()
    assert [(row["metric_key"], row["value"]) for row in snapshot_facts] == [("views", 0.0)]


def test_publish_verification_rejects_cross_platform_url(conn, seed):
    from src.core.operating_feedback import OperatingFeedbackError, create_publish_verification

    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=?,published_at=? WHERE id=?",
        ("2026-11-04T02:00:00+00:00", "2026-11-04T02:00:00+00:00", draft_id),
    )
    conn.commit()

    with pytest.raises(OperatingFeedbackError, match="域名与草稿所属平台不匹配"):
        create_publish_verification(
            conn, tenant_id="tenant-test", draft_id=draft_id,
            platform_content_id="foreign-platform-id",
            content_url="https://www.douyin.com/video/123",
            published_at="2026-11-04T02:00:00+00:00",
        )


def test_publish_snapshot_hashes_images_without_exposing_server_paths(conn, seed, tmp_path):
    import hashlib
    import json

    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="user_published", fetch_status=None)
    image = tmp_path / "published-card.png"
    image.write_bytes(b"original-published-image")
    conn.execute(
        "UPDATE contents SET image_paths=? WHERE id=(SELECT content_id FROM drafts WHERE id=?)",
        (json.dumps([str(image)]), draft_id),
    )
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=?,published_at=? WHERE id=?",
        ("2026-11-04T02:00:00+00:00", "2026-11-04T02:00:00+00:00", draft_id),
    )
    conn.commit()

    result = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id="note-image-hash",
        content_url="https://www.xiaohongshu.com/explore/image-hash",
        published_at="2026-11-04T02:00:00+00:00",
    )

    snapshot = result["content_snapshot"]
    assert snapshot["image_paths"] == ["published-card.png"]
    assert snapshot["image_files"][0]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert str(tmp_path) not in json.dumps(snapshot)


def test_manual_retry_starts_a_new_cycle_and_clears_stale_error(conn, seed):
    from src.core.operating_feedback import create_collection_run, create_publish_verification, retry_review_task

    round_id = seed.round(status="completed")
    draft_id = seed.draft(round_id, platform="xhs", draft_status="user_published", fetch_status=None)
    conn.execute(
        "UPDATE drafts SET user_confirmed_at=?,published_at=? WHERE id=?",
        ("2026-11-04T02:00:00+00:00", "2026-11-04T02:00:00+00:00", draft_id),
    )
    conn.commit()
    verification = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id="note-retry-cycle",
        content_url="https://www.xiaohongshu.com/explore/retry-cycle",
        published_at="2026-11-04T02:00:00+00:00",
    )
    task_id = verification["review_task_id"]
    create_collection_run(
        conn, task_id=task_id, source="platform_readback", status="partial",
        metrics={"views": 5}, timezone_name="Asia/Shanghai",
        error_message="评论数据暂不可用", idempotency_key="partial-run-1",
    )
    old = conn.execute("SELECT collection_cycle_id FROM review_tasks WHERE task_id=?", (task_id,)).fetchone()[0]

    retried = retry_review_task(conn, task_id)

    assert retried["status"] == "pending"
    assert retried["attempt_count"] == 0
    assert retried["collection_cycle_id"] != old
    assert retried["last_error"] is None
    assert retried["conclusion_json"] == "{}"


def test_operating_feedback_persists_explicit_audit_actors(conn, seed):
    draft_id, published_at = _published_draft(conn, seed)
    verification = create_publish_verification(
        conn, tenant_id="tenant-test", draft_id=draft_id,
        platform_content_id="audit-note-123",
        content_url="https://www.xiaohongshu.com/explore/audit-note-123",
        published_at=published_at, idempotency_key="audit-publish-123",
        actor="session:publisher",
    )
    run = create_collection_run(
        conn, task_id=verification["review_task_id"], source="manual",
        status="succeeded", metrics={"views": 0}, idempotency_key="actor-collection",
        actor="session:collector",
    )
    save_review_conclusion(
        conn, task_id=verification["review_task_id"], conclusion="复盘完成",
        evidence={"views": 0}, target_impact={}, recommendations=[],
        gap_changes=[{
            "gap_key": "actor-audit-gap", "title": "审计主体测试",
            "severity": "low", "status": "active",
        }],
        actor="session:reviewer",
    )
    retry_row = conn.execute(
        "SELECT recorded_by FROM review_collection_runs WHERE run_id=?", (run["run_id"],)
    ).fetchone()
    publish_row = conn.execute(
        "SELECT verified_by FROM content_publish_verifications WHERE verification_id=?",
        (verification["verification_id"],),
    ).fetchone()
    task_row = conn.execute(
        "SELECT created_by,conclusion_by FROM review_tasks WHERE task_id=?",
        (verification["review_task_id"],),
    ).fetchone()
    gap_event = conn.execute(
        "SELECT actor FROM business_gap_events WHERE review_task_id=?",
        (verification["review_task_id"],),
    ).fetchone()

    assert publish_row["verified_by"] == "session:publisher"
    assert task_row["created_by"] == "session:publisher"
    assert retry_row["recorded_by"] == "session:collector"
    assert task_row["conclusion_by"] == "session:reviewer"
    assert gap_event["actor"] == "session:reviewer"
