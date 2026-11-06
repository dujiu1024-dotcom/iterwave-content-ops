"""T8-B API acceptance for content review, publish confirmation and effect feedback."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.core.metrics import Normalizer
from src.core.notify import Notifier
from src.core.plan_version import PlanVersionManager
from src.core.state_machine import DraftStateMachine, RoundStateMachine


@pytest.fixture()
def content_client(cfg, conn, seed, test_auth):
    saved = dict(api_main._g_ctx)
    api_main._g_ctx.clear()
    api_main._g_ctx.update({
        "cfg": cfg,
        "conn": conn,
        "sm_draft": DraftStateMachine(conn, cfg=cfg),
        "sm_round": RoundStateMachine(conn, cfg=cfg),
        "plan_version_manager": PlanVersionManager(cfg, conn),
        "normalizer": Normalizer(conn, cfg),
        "notifier": Notifier(cfg, conn),
        "runtime_tenant_id": "default",
    })
    app = api_main._build_app()
    try:
        yield test_auth(TestClient(app, base_url="http://127.0.0.1")), conn, seed
    finally:
        api_main._g_ctx.clear()
        api_main._g_ctx.update(saved)


def _stage_for_round(conn, round_id: int) -> int:
    """Create the minimum real plan/version/stage provenance used by feedback APIs."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    master = conn.execute(
        """INSERT INTO master_plan_versions(
            version_no,plan_signature,plan_snapshot,status,source,created_at,created_by
        ) VALUES(1,?,?, 'draft','system',?,'t8-b-test')""",
        ("t8-b-master", json.dumps({"fixture": "t8-b"}), now),
    ).lastrowid
    subplan_id = "t8-b-subplan"
    conn.execute(
        """INSERT INTO sub_plans(
            id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
        ) VALUES(?,?,'xhs','T8-B 验收子计划','active',?,?,'t8-b-test')""",
        (subplan_id, master, now, now),
    )
    version = conn.execute(
        """INSERT INTO sub_plan_versions(
            sub_plan_id,version_no,master_plan_version_id,snapshot_json,status,created_at,created_by
        ) VALUES(?,1,?,?,'draft',?,'t8-b-test')""",
        (subplan_id, master, json.dumps({"fixture": "t8-b"}), now),
    ).lastrowid
    conn.execute(
        "UPDATE sub_plans SET current_version_id=? WHERE id=?",
        (version, subplan_id),
    )
    stage = conn.execute(
        """INSERT INTO sub_plan_stages(
            sub_plan_version_id,stage_order,title,target_count,status,created_at,updated_at,
            stage_key,strategy_goal,execution_strategy,effect_required,effect_status
        ) VALUES(?,1,'T8-B 内容效果验收',1,'pending',?,?,
                 't8-b-stage','验证发布效果回流','API 集成验收',1,'pending')""",
        (version, now, now),
    ).lastrowid
    conn.execute(
        "UPDATE spiral_rounds SET sub_plan_id=?,sub_plan_version_id=?,"
        "sub_plan_stage_id=?,master_plan_version_id=? WHERE id=?",
        (subplan_id, version, stage, master, round_id),
    )
    conn.commit()
    return int(stage)


def test_content_review_publish_metrics_and_effect_feedback_api_chain(content_client):
    client, conn, seed = content_client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending",
        fetch_status=None,
    )
    content = conn.execute(
        "SELECT id FROM contents WHERE round_id=?", (round_id,),
    ).fetchone()
    content_id = content["id"]
    conn.execute(
        "UPDATE contents SET guard_outcome='pass',guard_reason=NULL WHERE id=?",
        (content_id,),
    )
    stage_id = _stage_for_round(conn, round_id)
    conn.commit()

    # Metrics are gated until the user records that publication really happened.
    premature = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json={"views": 120, "likes": 12},
    )
    assert premature.status_code == 409

    approved = client.post(
        f"/api/contents/{content_id}/approve",
        json={"reason": "T8-B API 验收通过"},
    )
    assert approved.status_code == 200
    assert approved.json()["review_status"] == "approved"
    repeated_approval = client.post(
        f"/api/contents/{content_id}/approve",
        json={"reason": "重复审核请求"},
    )
    assert repeated_approval.status_code == 200
    assert repeated_approval.json()["deduped"] is True
    assert conn.execute(
        "SELECT COUNT(*) FROM content_review_events WHERE content_id=? AND event_type='approved'",
        (content_id,),
    ).fetchone()[0] == 1

    published = client.post(
        f"/api/drafts/{draft_id}/publish_confirm",
        json={"note_url": "https://www.xiaohongshu.com/explore/t8b-test"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "user_published"
    repeated_publish = client.post(
        f"/api/drafts/{draft_id}/publish_confirm",
        json={"note_url": "https://www.xiaohongshu.com/explore/t8b-test"},
    )
    assert repeated_publish.status_code == 200, repeated_publish.text
    assert repeated_publish.json().get("already_confirmed") is True
    assert conn.execute(
        "SELECT COUNT(*) FROM content_events WHERE content_id=? AND event_type='publish_confirmed'",
        (content_id,),
    ).fetchone()[0] == 1

    metrics = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json={"views": 120, "likes": 12, "collects": 8, "comments": 3},
        headers={"Idempotency-Key": "t8b-manual-metrics-original"},
    )
    assert metrics.status_code == 200, metrics.text
    snapshot_id = metrics.json()["snapshot_id"]
    repeated_metrics = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json={"views": 120, "likes": 12, "collects": 8, "comments": 3},
        headers={"Idempotency-Key": "t8b-manual-metrics-original"},
    )
    assert repeated_metrics.status_code == 200, repeated_metrics.text
    assert repeated_metrics.json()["snapshot_id"] == snapshot_id
    assert repeated_metrics.json()["idempotent_replay"] is True
    conflicting_metrics = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json={"views": 121, "likes": 12, "collects": 8, "comments": 3},
        headers={"Idempotency-Key": "t8b-manual-metrics-original"},
    )
    assert conflicting_metrics.status_code == 409, conflicting_metrics.text
    assert conn.execute(
        "SELECT COUNT(*) FROM content_events WHERE content_id=? AND event_type='metrics_submitted'",
        (content_id,),
    ).fetchone()[0] == 1
    snapshots = client.get(
        "/api/metrics/snapshots", params={"draft_id": draft_id},
    )
    assert snapshots.status_code == 200
    assert snapshots.json()["total"] == 1
    saved_metric = snapshots.json()["items"][0]
    assert saved_metric["id"] == snapshot_id
    assert saved_metric["content_id"] == content_id
    assert saved_metric["platform"] == "xhs"
    assert saved_metric["source"] == "manual"
    manual_interaction_rate = conn.execute(
        "SELECT value FROM metric_facts_latest WHERE content_id=? "
        "AND metric_key='interaction_rate'",
        (content_id,),
    ).fetchone()
    assert manual_interaction_rate is not None
    assert abs(manual_interaction_rate["value"] - (12 + 8 + 3) / 120) < 1e-9

    feedback_before = client.get(
        f"/api/subplans/stages/{stage_id}/effect-feedback",
    )
    assert feedback_before.status_code == 200, feedback_before.text
    payload = feedback_before.json()
    assert payload["content_count"] == 1
    assert payload["published_count"] == 1
    assert payload["manual_metrics"]["samples"] == 1
    assert payload["manual_metrics"]["views"] == 120.0

    recorded = client.post(
        f"/api/subplans/stages/{stage_id}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "保持当前选题方向，并增加同类内容样本。",
            "evidence_note": "来自已确认发布内容的人工回填数据。",
            "confidence": "medium",
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["stage_id"] == stage_id
    feedback_after = client.get(
        f"/api/subplans/stages/{stage_id}/effect-feedback",
    )
    assert feedback_after.status_code == 200
    assert len(feedback_after.json()["reviews"]) == 1
    evidence = json.loads(feedback_after.json()["reviews"][0]["evidence_json"])
    assert evidence["published_count"] == 1
    assert evidence["manual_metrics"]["views"] == 120.0

    # Activate the fixture versions before exercising the effective-version-only review API.
    source_version = conn.execute(
        "SELECT sub_plan_version_id FROM sub_plan_stages WHERE id=?", (stage_id,),
    ).fetchone()["sub_plan_version_id"]
    master_version = conn.execute(
        "SELECT master_plan_version_id FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["master_plan_version_id"]
    conn.execute("UPDATE master_plan_versions SET status='effective' WHERE id=?", (master_version,))
    conn.execute("UPDATE sub_plan_versions SET status='effective' WHERE id=?", (source_version,))
    conn.commit()

    # The human effect-gate decision must reference this exact frozen feedback.
    wrong_source = client.post(
        f"/api/subplans/t8-b-subplan/stages/{stage_id}/condition-review",
        json={
            "condition_type": "effect",
            "decision": "passed",
            "reason": "核验效果反馈",
            "effect_review_id": 999999,
        },
    )
    assert wrong_source.status_code == 404, wrong_source.text
    assert conn.execute(
        "SELECT effect_status FROM sub_plan_stages WHERE id=?", (stage_id,)
    ).fetchone()["effect_status"] == "pending"
    mismatched_decision = client.post(
        f"/api/subplans/t8-b-subplan/stages/{stage_id}/condition-review",
        json={
            "condition_type": "effect",
            "decision": "failed",
            "reason": "故意验证不一致结论被拒绝",
            "effect_review_id": recorded.json()["id"],
        },
    )
    assert mismatched_decision.status_code == 409, mismatched_decision.text
    assert conn.execute(
        "SELECT effect_status FROM sub_plan_stages WHERE id=?", (stage_id,)
    ).fetchone()["effect_status"] == "pending"
    confirmed = client.post(
        f"/api/subplans/t8-b-subplan/stages/{stage_id}/condition-review",
        json={
            "condition_type": "effect",
            "decision": "passed",
            "reason": "人工确认指标证据达到目标",
            "recommendation": "维持当前策略并继续观察。",
            "confidence": "medium",
            "effect_review_id": recorded.json()["id"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["stage"]["effect_status"] == "passed"
    assert confirmed.json()["completion_gates"]["effect_status"] == "passed"
    assert confirmed.json()["completion_gates"]["production_blocked"] is False
    reviews = client.get(
        f"/api/subplans/t8-b-subplan/stages/{stage_id}/condition-reviews",
    )
    assert reviews.status_code == 200, reviews.text
    condition_evidence = reviews.json()["condition_reviews"][0]["evidence"]
    assert condition_evidence["source_effect_review_id"] == recorded.json()["id"]
    assert condition_evidence["source_evidence"]["manual_metrics"]["views"] == 120.0

    # A feedback-derived adjustment must retain the exact review/evidence provenance
    # and create a draft only; it must not mutate the effective sub-plan in place.
    original_snapshot = conn.execute(
        "SELECT snapshot_json FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["snapshot_json"]

    missing_source = client.post(
        "/api/plan_adjustments/adopt",
        json={"dimensions": ["内容结构"], "effect_review_id": 999999},
    )
    assert missing_source.status_code == 404

    adopted = client.post(
        "/api/plan_adjustments/adopt",
        json={"dimensions": ["内容结构"], "effect_review_id": recorded.json()["id"]},
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["effective"] is False
    assert adopted.json()["requires_human_review"] is True
    assert len(adopted.json()["drafts"]) == 1
    draft_version = adopted.json()["drafts"][0]["draft_version"]
    provenance = json.loads(draft_version["snapshot_json"])["review_adoptions"][-1]["effect_feedback"]
    assert provenance["id"] == recorded.json()["id"]
    assert provenance["stage_id"] == stage_id
    assert provenance["stage_key"] == "t8-b-stage"
    assert provenance["source_sub_plan_version_id"] == source_version
    assert provenance["recommendation"] == "保持当前选题方向，并增加同类内容样本。"
    assert provenance["evidence"]["manual_metrics"]["views"] == 120.0
    assert conn.execute(
        "SELECT snapshot_json FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["snapshot_json"] == original_snapshot

    # Evidence-insufficient feedback must not create an adoptable plan draft.
    insufficient = client.post(
        f"/api/subplans/stages/{stage_id}/effect-feedback",
        json={
            "assessment": "insufficient_data",
            "recommendation": "继续收集样本后再判断。",
            "evidence_note": "当前样本量不足。",
            "confidence": "low",
        },
    )
    assert insufficient.status_code == 201, insufficient.text
    versions_before_rejection = conn.execute(
        "SELECT COUNT(*) FROM sub_plan_versions WHERE sub_plan_id='t8-b-subplan'"
    ).fetchone()[0]
    blocked_adoption = client.post(
        "/api/plan_adjustments/adopt",
        json={
            "dimensions": ["内容结构"],
            "effect_review_id": insufficient.json()["id"],
        },
    )
    assert blocked_adoption.status_code == 409, blocked_adoption.text
    assert conn.execute(
        "SELECT COUNT(*) FROM sub_plan_versions WHERE sub_plan_id='t8-b-subplan'"
    ).fetchone()[0] == versions_before_rejection


    # A different key denotes a genuinely new time-series measurement.
    second_measurement = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json={"views": 150, "likes": 15, "collects": 9, "comments": 4},
        headers={"Idempotency-Key": "t8b-manual-metrics-second"},
    )
    assert second_measurement.status_code == 200, second_measurement.text
    assert second_measurement.json()["snapshot_id"] != snapshot_id

    # Corrections are append-only and the same correction key is retry-safe.
    correction_payload = {
        "views": 145, "likes": 14, "collects": 9, "comments": 4,
        "correction_of": snapshot_id,
    }
    correction = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json=correction_payload,
        headers={"Idempotency-Key": "t8b-manual-metrics-correction"},
    )
    assert correction.status_code == 200, correction.text
    repeated_correction = client.post(
        f"/api/drafts/{draft_id}/metrics/manual",
        json=correction_payload,
        headers={"Idempotency-Key": "t8b-manual-metrics-correction"},
    )
    assert repeated_correction.status_code == 200, repeated_correction.text
    assert repeated_correction.json()["snapshot_id"] == correction.json()["snapshot_id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM metric_snapshots WHERE tenant_id='default' AND draft_id=?",
        (draft_id,),
    ).fetchone()[0] == 3


def test_effect_feedback_isolated_between_subplans_with_same_stage_key(content_client):
    client, conn, seed = content_client
    own_round = seed.round(status="awaiting_publish")
    seed.draft(own_round, platform="xhs", draft_status="draft_pending", fetch_status=None)
    own_content = conn.execute(
        "SELECT id FROM contents WHERE round_id=?", (own_round,),
    ).fetchone()["id"]
    own_stage = _stage_for_round(conn, own_round)

    # A different sub-plan deliberately reuses the same stable stage key.
    other_round = seed.round(status="awaiting_publish")
    seed.draft(other_round, platform="xhs", draft_status="draft_pending", fetch_status=None)
    other_content = conn.execute(
        "SELECT id FROM contents WHERE round_id=?", (other_round,),
    ).fetchone()["id"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    next_master_version = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM master_plan_versions"
    ).fetchone()[0]
    master = conn.execute(
        """INSERT INTO master_plan_versions(
            version_no,plan_signature,plan_snapshot,status,source,created_at,created_by
        ) VALUES(?,?,?, 'draft','system',?,'isolation-test')""",
        (next_master_version, "t8-isolation-master", json.dumps({"fixture": "isolation"}), now),
    ).lastrowid
    conn.execute(
        """INSERT INTO sub_plans(
            id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
        ) VALUES('t8-other-subplan',?,'xhs','隔离测试子计划','active',?,?,'isolation-test')""",
        (master, now, now),
    )
    other_version = conn.execute(
        """INSERT INTO sub_plan_versions(
            sub_plan_id,version_no,master_plan_version_id,snapshot_json,status,created_at,created_by
        ) VALUES('t8-other-subplan',1,?,?,'draft',?,'isolation-test')""",
        (master, json.dumps({"fixture": "other"}), now),
    ).lastrowid
    other_stage = conn.execute(
        """INSERT INTO sub_plan_stages(
            sub_plan_version_id,stage_order,title,target_count,status,created_at,updated_at,
            stage_key,effect_required,effect_status
        ) VALUES(?,1,'同名稳定步骤',1,'pending',?,?,'t8-b-stage',1,'pending')""",
        (other_version, now, now),
    ).lastrowid
    conn.execute(
        "UPDATE spiral_rounds SET sub_plan_id='t8-other-subplan',sub_plan_version_id=?,"
        "sub_plan_stage_id=?,master_plan_version_id=? WHERE id=?",
        (other_version, other_stage, master, other_round),
    )
    conn.commit()

    other_review = client.post(
        f"/api/subplans/stages/{other_stage}/effect-feedback",
        json={
            "assessment": "insufficient_data",
            "recommendation": "仅属于另一个子计划。",
            "evidence_note": "隔离回归测试。",
            "confidence": "low",
        },
    )
    assert other_review.status_code == 201, other_review.text

    feedback = client.get(f"/api/subplans/stages/{own_stage}/effect-feedback")
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["content_count"] == 1
    assert feedback.json()["published_count"] == 0
    assert feedback.json()["manual_metrics"]["samples"] == 0
    assert feedback.json()["reviews"] == []
    assert own_content != other_content

    recorded = client.post(
        f"/api/subplans/stages/{own_stage}/effect-feedback",
        json={
            "assessment": "insufficient_data",
            "recommendation": "继续收集本子计划样本。",
            "evidence_note": "隔离回归测试。",
            "confidence": "low",
        },
    )
    assert recorded.status_code == 201, recorded.text
    saved = client.get(f"/api/subplans/stages/{own_stage}/effect-feedback")
    assert saved.status_code == 200, saved.text
    assert len(saved.json()["reviews"]) == 1


def test_content_review_rejection_requires_resubmission_before_approval(content_client):
    client, conn, seed = content_client
    round_id = seed.round(status="producing")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending",
        fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT id FROM contents WHERE round_id=?", (round_id,),
    ).fetchone()["id"]
    conn.execute(
        "UPDATE contents SET guard_outcome='pass',guard_reason=NULL WHERE id=?",
        (content_id,),
    )
    conn.commit()

    rejected = client.post(
        f"/api/contents/{content_id}/reject",
        json={"reason": "缺少关键事实依据"},
    )
    assert rejected.status_code == 200, rejected.text
    blocked = client.post(
        f"/api/contents/{content_id}/approve",
        json={"reason": "不应直接通过"},
    )
    assert blocked.status_code == 409
    resubmitted = client.post(
        f"/api/contents/{content_id}/resubmit",
        json={"reason": "已补充事实依据"},
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["review_rejected"] is False
    assert conn.execute(
        "SELECT review_rejected FROM contents WHERE id=?", (content_id,),
    ).fetchone()["review_rejected"] == 0
    assert conn.execute(
        "SELECT status FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()["status"] == "draft_pending"


def test_feedback_adoption_requires_formal_version_review_and_preserves_history(content_client):
    client, conn, seed = content_client
    round_id = seed.round(status="awaiting_publish")
    seed.draft(round_id, platform="xhs", draft_status="draft_pending", fetch_status=None)
    content_id = conn.execute(
        "SELECT id FROM contents WHERE round_id=?", (round_id,),
    ).fetchone()["id"]
    conn.execute(
        "UPDATE contents SET guard_outcome='pass',guard_reason=NULL WHERE id=?",
        (content_id,),
    )
    stage_id = _stage_for_round(conn, round_id)
    source_version = conn.execute(
        "SELECT sub_plan_version_id FROM sub_plan_stages WHERE id=?", (stage_id,),
    ).fetchone()["sub_plan_version_id"]
    master_version = conn.execute(
        "SELECT master_plan_version_id FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["master_plan_version_id"]
    conn.execute("UPDATE master_plan_versions SET status='effective' WHERE id=?", (master_version,))
    conn.execute("UPDATE sub_plan_versions SET status='effective' WHERE id=?", (source_version,))
    conn.commit()

    feedback = client.post(
        f"/api/subplans/stages/{stage_id}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "保持当前方向并扩大高表现选题样本。",
            "evidence_note": "正式版本治理回归测试。",
            "confidence": "high",
        },
    )
    assert feedback.status_code == 201, feedback.text
    review = client.post(
        f"/api/subplans/t8-b-subplan/stages/{stage_id}/condition-review",
        json={
            "condition_type": "effect",
            "decision": "passed",
            "reason": "人工确认反馈证据",
            "recommendation": "保持当前方向并扩大高表现选题样本。",
            "confidence": "high",
            "effect_review_id": feedback.json()["id"],
        },
    )
    assert review.status_code == 200, review.text

    original_snapshot = conn.execute(
        "SELECT snapshot_json FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["snapshot_json"]
    original_round_binding = conn.execute(
        "SELECT sub_plan_version_id,sub_plan_stage_id FROM spiral_rounds WHERE id=?",
        (round_id,),
    ).fetchone()

    created = client.post(
        "/api/plan_adjustments/adopt",
        json={"dimensions": ["选题方向"], "effect_review_id": feedback.json()["id"]},
    )
    assert created.status_code == 200, created.text
    assert created.json()["effective"] is False
    draft_version = created.json()["drafts"][0]["draft_version"]
    draft_version_id = int(draft_version["id"])

    # Adoption creates a proposal only; the effective version must not move until
    # the dedicated sub-plan review API approves this exact draft.
    current_before_review = conn.execute(
        "SELECT current_version_id FROM sub_plans WHERE id='t8-b-subplan'",
    ).fetchone()["current_version_id"]
    assert int(current_before_review) == int(source_version)
    approved = client.post(
        "/api/subplans/t8-b-subplan/review",
        json={"version_id": draft_version_id, "action": "approve"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["authorization"] == "active"
    current_after_review = conn.execute(
        "SELECT current_version_id FROM sub_plans WHERE id='t8-b-subplan'",
    ).fetchone()["current_version_id"]
    assert int(current_after_review) == draft_version_id
    assert conn.execute(
        "SELECT status FROM sub_plan_versions WHERE id=?", (draft_version_id,),
    ).fetchone()["status"] == "effective"

    # A previously created round remains pinned to the original version and stage;
    # activating a new version cannot rewrite its historical provenance.
    historical_binding = conn.execute(
        "SELECT sub_plan_version_id,sub_plan_stage_id FROM spiral_rounds WHERE id=?",
        (round_id,),
    ).fetchone()
    assert tuple(historical_binding) == tuple(original_round_binding)
    assert conn.execute(
        "SELECT snapshot_json FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["snapshot_json"] == original_snapshot

    # A later proposal from feedback on the new effective version can be rejected
    # without replacing the version that was already approved above.
    new_stage_id = int(conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order LIMIT 1",
        (draft_version_id,),
    ).fetchone()["id"])
    new_feedback = client.post(
        f"/api/subplans/stages/{new_stage_id}/effect-feedback",
        json={
            "assessment": "effective",
            "recommendation": "调整发布节奏后继续观察。",
            "evidence_note": "新生效版本的独立反馈。",
            "confidence": "medium",
        },
    )
    assert new_feedback.status_code == 201, new_feedback.text
    new_review = client.post(
        f"/api/subplans/t8-b-subplan/stages/{new_stage_id}/condition-review",
        json={
            "condition_type": "effect",
            "decision": "passed",
            "reason": "确认新版本反馈",
            "recommendation": "调整发布节奏后继续观察。",
            "confidence": "medium",
            "effect_review_id": new_feedback.json()["id"],
        },
    )
    assert new_review.status_code == 200, new_review.text
    second = client.post(
        "/api/plan_adjustments/adopt",
        json={"dimensions": ["发布节奏"], "effect_review_id": new_feedback.json()["id"]},
    )
    assert second.status_code == 200, second.text
    rejected_version_id = int(second.json()["drafts"][0]["draft_version"]["id"])
    rejected = client.post(
        "/api/subplans/t8-b-subplan/review",
        json={
            "version_id": rejected_version_id,
            "action": "reject",
            "reason": "本轮不采纳该调整建议",
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["authorization"] == "not_authorized"
    assert int(conn.execute(
        "SELECT current_version_id FROM sub_plans WHERE id='t8-b-subplan'",
    ).fetchone()["current_version_id"]) == draft_version_id
    assert conn.execute(
        "SELECT status FROM sub_plan_versions WHERE id=?", (rejected_version_id,),
    ).fetchone()["status"] == "rejected"


def test_business_summary_counts_latest_metric_once_per_content_platform(content_client):
    """多个 draft 指向同一篇同平台内容时，经营汇总仍按篇取最新指标。"""
    client, conn, seed = content_client
    round_id = seed.round(status="awaiting_publish")
    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="published", fetch_status=None,
    )
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()["content_id"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 模拟同一内容在历史重试/重建过程中留下两个 draft 关联的指标事实。
    # 有效事实层按 content + platform + metric 取最新；经营汇总必须与其一致。
    for fact_id, fact_draft_id, value, measured_at in (
        ("summary-old", draft_id, 100.0, "2026-11-05T08:00:00+00:00"),
        ("summary-new", f"{draft_id}-retry", 240.0, now),
    ):
        conn.execute(
            "INSERT INTO metric_facts "
            "(fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,"
            "source_record_type,source_record_id,measured_at,recorded_at,timezone_name,"
            "collection_status,created_at) "
            "VALUES (?,?,?,?,?,'views',?,'count','platform_readback','review_collection_run',"
            "?,?,?,'UTC','succeeded',?)",
            (fact_id, "default", fact_draft_id, content_id, "xhs", value,
             fact_id, measured_at, now, now),
        )
    conn.commit()

    response = client.get("/api/analytics/business-summary", params={"days": 365})
    assert response.status_code == 200, response.text
    interactions = response.json()["interactions"]
    assert interactions["sample_count"] == 1
    assert interactions["views"] == 240
    assert interactions["known_samples"]["views"] == 1
