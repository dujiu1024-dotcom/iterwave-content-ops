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
            stage_key,strategy_goal,execution_strategy,effect_required
        ) VALUES(?,1,'T8-B 内容效果验收',1,'pending',?,?,
                 't8-b-stage','验证发布效果回流','API 集成验收',1)""",
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
    )
    assert metrics.status_code == 200, metrics.text
    snapshot_id = metrics.json()["snapshot_id"]
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
    source_version = conn.execute(
        "SELECT sub_plan_version_id FROM sub_plan_stages WHERE id=?", (stage_id,),
    ).fetchone()["sub_plan_version_id"]
    master_version = conn.execute(
        "SELECT master_plan_version_id FROM sub_plan_versions WHERE id=?", (source_version,),
    ).fetchone()["master_plan_version_id"]
    conn.execute("UPDATE master_plan_versions SET status='effective' WHERE id=?", (master_version,))
    conn.execute("UPDATE sub_plan_versions SET status='effective' WHERE id=?", (source_version,))
    conn.commit()
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
