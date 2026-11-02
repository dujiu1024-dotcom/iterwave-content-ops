from __future__ import annotations

import pytest

from src.core.plan_version import PlanVersionManager
from src.core.task_snapshot import (
    StaleTaskSnapshot,
    build_execution_snapshot,
    sanitize_snapshot,
    validate_execution_snapshot,
)


def test_task_snapshot_removes_credentials_but_keeps_numeric_token_limits():
    safe = sanitize_snapshot({
        "api_key": "secret-api-key",
        "openai_api_key": "secret-openai-key",
        "password": "secret-password",
        "cookies": [{"name": "session", "value": "private"}],
        "max_tokens": 4096,
        "nested": {"client_secret": "private", "temperature": 0.7},
    })
    assert safe == {"max_tokens": 4096, "nested": {"temperature": 0.7}}


def test_execution_snapshot_validates_config_and_plan_versions(cfg, conn, seed):
    round_id = seed.round(status="created")
    snapshot = build_execution_snapshot(
        cfg, conn, round_id=round_id,
        parameters={"round_id": round_id, "topic_count": 5, "platforms": ["xhs"]},
    )
    validate_execution_snapshot(snapshot, cfg, conn)

    # D4: paths/output_dir 属于运行目录配置，不参与生产任务指纹。
    # 修改真实生产字段，验证排队任务仍会因生产行为变化而失效。
    cfg.platforms.kuaishou.enabled = not cfg.platforms.kuaishou.enabled
    with pytest.raises(StaleTaskSnapshot, match="配置已变化"):
        validate_execution_snapshot(snapshot, cfg, conn)


def test_execution_snapshot_rejects_changed_master_plan(cfg, conn, seed):
    round_id = seed.round(status="created")
    snapshot = build_execution_snapshot(
        cfg, conn, round_id=round_id, parameters={"round_id": round_id},
    )
    manager = PlanVersionManager(cfg, conn)
    plan = manager.load_plan()[0]
    plan["topic_count_per_platform"] = int(plan["topic_count_per_platform"]) + 1
    manager.save_plan(plan)

    with pytest.raises(StaleTaskSnapshot, match="母计划已变化"):
        validate_execution_snapshot(snapshot, cfg, conn)


def test_enqueue_rejects_round_already_based_on_old_plan(cfg, conn, seed):
    round_id = seed.round(status="created")
    manager = PlanVersionManager(cfg, conn)
    original_plan = manager.load_plan()[0]
    manager.record(round_id, original_plan)
    changed_plan = dict(original_plan)
    changed_plan["topic_count_per_platform"] = int(original_plan["topic_count_per_platform"]) + 1
    manager.save_plan(changed_plan)

    with pytest.raises(StaleTaskSnapshot, match="目标轮次已基于旧母计划"):
        build_execution_snapshot(
            cfg, conn, round_id=round_id, parameters={"round_id": round_id},
        )



def test_queued_subplan_task_is_rejected_after_confirmed_publish_target_reached(cfg, conn):
    import json
    from datetime import datetime, timezone
    from src.core.plan_version_store import PlanVersionStore

    store = PlanVersionStore(conn)
    active = conn.execute(
        "SELECT id,plan_snapshot FROM master_plan_versions WHERE status='effective' LIMIT 1"
    ).fetchone()
    if active is None:
        plan = PlanVersionManager(cfg, conn).load_plan()[0]
        plan["enabled_platforms"] = list(dict.fromkeys([*(plan.get("enabled_platforms") or []), "xhs"]))
        draft = store.create_master_draft(plan)
        master_id = int(store.activate_master_version(draft["id"], reviewer="test")["id"])
    else:
        master_id = int(active["id"])
        plan = json.loads(active["plan_snapshot"])
        if "xhs" not in (plan.get("enabled_platforms") or []):
            plan["enabled_platforms"] = list(dict.fromkeys([*(plan.get("enabled_platforms") or []), "xhs"]))
            draft = store.create_master_draft(plan, based_on_version_id=master_id)
            master_id = int(store.activate_master_version(draft["id"], reviewer="test")["id"])

    sub_id = "task-snapshot-publish-target"
    store.create_subplan(sub_id, master_plan_version_id=master_id, platform="xhs", title="quota test")
    version = store.create_subplan_version(
        sub_id, {"title": "quota test", "platform": "xhs"},
        master_plan_version_id=master_id,
        stages=[{"title": "single publish", "target_count": 1}],
    )
    store.activate_subplan_version(version["id"], reviewer="test")
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()
    key = "task-snapshot-quota-test"
    store.reserve_round_open(
        key, "quota-test-hash", master_plan_version_id=master_id,
        sub_plan_id=sub_id, sub_plan_version_id=int(version["id"]),
        sub_plan_stage_id=int(stage["id"]),
    )
    created = store.create_round_once(
        key, content_variants=[{"platform": "xhs"}], trigger_source="manual",
    )
    round_id = int(created["round_id"])
    snapshot = build_execution_snapshot(
        cfg, conn, round_id=round_id, parameters={"round_id": round_id},
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO contents(id,platform,title,body,round_id,created_at) VALUES(?,?,?,?,?,?)",
        ("quota-content", "xhs", "confirmed", "body", round_id, now),
    )
    conn.execute(
        "INSERT INTO drafts(id,content_id,platform,status,user_confirmed_at,created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("quota-draft", "quota-content", "xhs", "user_published", now, now),
    )
    conn.commit()

    with pytest.raises(StaleTaskSnapshot, match="发布目标已达成"):
        validate_execution_snapshot(snapshot, cfg, conn)



def test_queued_subplan_round_fails_closed_after_parent_master_superseded(cfg, conn):
    import json
    from src.core.plan_version_store import PlanVersionStore

    store = PlanVersionStore(conn)
    active = conn.execute(
        "SELECT id,plan_snapshot FROM master_plan_versions WHERE status='effective' LIMIT 1"
    ).fetchone()
    if active is None:
        plan = PlanVersionManager(cfg, conn).load_plan()[0]
        plan["enabled_platforms"] = list(dict.fromkeys([*(plan.get("enabled_platforms") or []), "xhs"]))
        draft = store.create_master_draft(plan)
        master_id = int(store.activate_master_version(draft["id"], reviewer="test")["id"])
        plan_snapshot = plan
    else:
        master_id = int(active["id"])
        plan_snapshot = json.loads(active["plan_snapshot"])
        if "xhs" not in (plan_snapshot.get("enabled_platforms") or []):
            plan_snapshot["enabled_platforms"] = list(dict.fromkeys([*(plan_snapshot.get("enabled_platforms") or []), "xhs"]))
            draft = store.create_master_draft(plan_snapshot, based_on_version_id=master_id)
            master_id = int(store.activate_master_version(draft["id"], reviewer="test")["id"])

    sub_id = "queued-old-master-subplan"
    store.create_subplan(sub_id, master_plan_version_id=master_id, platform="xhs", title="old master queue")
    version = store.create_subplan_version(
        sub_id, {"title": "old master queue", "platform": "xhs"},
        master_plan_version_id=master_id,
        stages=[{"title": "stage", "target_count": 5}],
    )
    store.activate_subplan_version(version["id"], reviewer="test")
    stage = conn.execute(
        "SELECT id FROM sub_plan_stages WHERE sub_plan_version_id=?", (version["id"],)
    ).fetchone()
    key = "queued-old-master-round"
    store.reserve_round_open(
        key, "queued-old-master-hash", master_plan_version_id=master_id,
        sub_plan_id=sub_id, sub_plan_version_id=int(version["id"]),
        sub_plan_stage_id=int(stage["id"]),
    )
    round_id = int(store.create_round_once(key, trigger_source="auto")["round_id"])
    snapshot = build_execution_snapshot(
        cfg, conn, round_id=round_id, parameters={"round_id": round_id},
    )

    old_plan = json.loads(conn.execute(
        "SELECT plan_snapshot FROM master_plan_versions WHERE id=?", (master_id,)
    ).fetchone()[0])
    old_plan["topic_count_per_platform"] = int(old_plan.get("topic_count_per_platform") or 5) + 1
    new_master = store.create_master_draft(old_plan, based_on_version_id=master_id)
    store.activate_master_version(new_master["id"], reviewer="test")

    # A activation pauses child plans tied to the superseded parent. The old round's
    # immutable attribution remains intact, but its queued task must fail closed.
    with pytest.raises(StaleTaskSnapshot, match="子计划已暂停"):
        validate_execution_snapshot(snapshot, cfg, conn)
    old_round = conn.execute(
        "SELECT master_plan_version_id,sub_plan_version_id FROM spiral_rounds WHERE id=?",
        (round_id,),
    ).fetchone()
    assert tuple(old_round) == (master_id, int(version["id"]))
