"""Regression tests for one-content production lines and immutable business context."""
import json
import sqlite3
from pathlib import Path

import pytest

from src.core.content_production_line_store import ContentProductionLineStore
from src.core.plan_version_store import PlanVersionStore
from src.core.production_line_store import ProductionLineStore
from src.core.production_task_store import ProductionTaskError, ProductionTaskStore
from src.core.production_context import ProductionContextError


def _create_round(conn, *, key, master_id, sub_id, sub_version_id, stage_id, line_version_id):
    store = PlanVersionStore(conn)
    store.reserve_round_open(
        key, "hash-" + key, master_plan_version_id=master_id,
        sub_plan_id=sub_id, sub_plan_version_id=sub_version_id,
        sub_plan_stage_id=stage_id,
    )
    created = store.create_round_once(
        key, content_variants=[{"platform": "xhs"}], trigger_source="manual",
        production_line_version_id=line_version_id,
    )
    return int(created["round_id"])


def _setup(conn):
    conn.execute(
        """INSERT INTO business_profiles(
            id,business_name,introduction,industry,business_model,business_stage,
            operating_goal,field_evidence_json,version_no,updated_at
        ) VALUES(1,'测试企业','提供测试产品与解决方案','教育科技','产品销售','成长期',
                 '获取有效咨询','{}',1,CURRENT_TIMESTAMP)"""
    )
    conn.execute(
        """INSERT INTO business_profile_versions(
            version_no,snapshot_json,change_note,created_at,created_by
        ) VALUES(1,'{"business_name":"测试企业"}','测试基线',CURRENT_TIMESTAMP,'test')"""
    )
    conn.execute(
        """INSERT INTO business_catalog_items(
            id,entity_type,name,details_json,field_evidence_json,created_at,updated_at
        ) VALUES('t3-product','product','测试产品','{}','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    conn.commit()
    store = PlanVersionStore(conn)
    master = store.create_master_draft({
        "topic_count_per_platform": 1,
        "dimension_allocation": {"AI教育": 1},
        "enabled_platforms": ["xhs"],
        "content_format": "image_text",
    })
    master_id = int(store.activate_master_version(master["id"], reviewer="task-3-test")["id"])
    north_star_snapshot = {"goal_text": "建立稳定的有效咨询量", "target_metric": "qualified_leads", "target_value": 100}
    north_star_cursor = conn.execute(
        """INSERT INTO account_group_north_star_versions(
            version_no,snapshot_json,change_reason,created_by,created_at,effective_at
        ) VALUES(1,?,'测试北极星基线','task-3-test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
        (json.dumps(north_star_snapshot, ensure_ascii=False),),
    )
    conn.execute(
        """UPDATE master_plan_versions
           SET north_star_version_id=?,north_star_snapshot_json=?,
               north_star_attribution_status='attributed'
           WHERE id=?""",
        (north_star_cursor.lastrowid, json.dumps(north_star_snapshot, ensure_ascii=False), master_id),
    )
    conn.execute(
        """INSERT INTO business_diagnosis_reports(
            version_no,status,report_json,source_snapshot_json,source_fingerprint,
            model_name,created_at,created_by,updated_at,confirmed_at,confirmed_by
        ) VALUES(1,'confirmed','{"business_gaps":[]}','{}','test-fingerprint',
                 'test-model',CURRENT_TIMESTAMP,'test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test')"""
    )
    conn.commit()

    sub_id = "t3-sub-xhs"
    store.create_subplan(
        sub_id, master_plan_version_id=master_id, platform="xhs",
        title="单内容生产测试子计划",
    )
    sub_version = store.create_subplan_version(
        sub_id, {"title": "单内容生产测试子计划", "platform": "xhs"},
        master_plan_version_id=master_id,
        stages=[{
            "title": "第一阶段", "target_count": 5, "direction": "AI教育",
            "strategy_goal": "建立精准受众认知", "dependencies": [],
        }],
    )
    store.activate_subplan_version(sub_version["id"], reviewer="task-3-test")
    stage = conn.execute(
        "SELECT * FROM sub_plan_stages WHERE sub_plan_version_id=?",
        (sub_version["id"],),
    ).fetchone()
    conn.execute(
        """INSERT INTO sub_goals(
            id,sub_plan_version_id,stage_id,goal_type,goal_order,priority,title,
            description,target_count,acceptance_criteria,requires_production,status,
            created_at,updated_at
        ) VALUES('t3-formal-subgoal',?,?,'quantity',1,3,'建立有效咨询内容触达',
                 '为本轮生产提供正式业务目标',5,'完成对应内容生产并保留验收证据',
                 1,'ready',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
        (int(sub_version["id"]), int(stage["id"])),
    )
    conn.execute(
        """INSERT INTO sub_goal_production_plans(
            goal_id,content_direction,suggested_count,resource_requirements_json,
            priority,execution_conditions,acceptance_relation,enabled,created_at,updated_at
        ) VALUES('t3-formal-subgoal','AI教育',1,'{}',3,'依赖通过后逐条生产',
                 '发布复盘后回看目标',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    line_store = ProductionLineStore(conn)
    line_draft = line_store.create_draft(
        sub_id, int(sub_version["id"]),
        {"title": "单内容测试生产模板", "stages": [{
            "stage_key": str(stage["stage_key"]), "batch_size": 1,
            "production_goal": str(stage["title"]),
        }]},
        generation_source="manual", author="task-3-test",
    )
    line_store.activate(int(line_draft["id"]), reviewer="task-3-test")
    return {
        "master_id": master_id, "sub_id": sub_id,
        "sub_version_id": int(sub_version["id"]),
        "stage_id": int(stage["id"]), "stage_key": str(stage["stage_key"]),
        "line_version_id": int(line_draft["id"]),
    }


def _create_task(conn, ids, key):
    round_id = _create_round(
        conn, key="round-" + key, master_id=ids["master_id"],
        sub_id=ids["sub_id"], sub_version_id=ids["sub_version_id"],
        stage_id=ids["stage_id"], line_version_id=ids["line_version_id"],
    )
    task = ProductionTaskStore(conn).create_task(
        round_id=round_id, master_plan_version_id=ids["master_id"],
        sub_plan_id=ids["sub_id"], sub_plan_version_id=ids["sub_version_id"],
        strategy_step_id=ids["stage_key"], platform="xhs", task_ordinal=1,
        idempotency_key=key,
        input_snapshot={
            "topic": {"id": "topic-" + key, "title": "本次独立选题"},
            "strategy_step": {"title": "第一阶段"},
            "previous_review_feedback": {"recommendations": ["增加真实案例"]},
            "original_assets": [],
        },
    )
    return round_id, task


def test_new_task_has_complete_immutable_context_and_line_lifecycle(conn, seed):
    ids = _setup(conn)
    round_id, task = _create_task(conn, ids, "task-a")
    lines = ContentProductionLineStore(conn)
    line = lines.get_for_task(task["id"])
    snapshot = line["context_snapshot"]
    assert line["status"] == "pending"
    assert snapshot["context_status"] == "complete"
    assert snapshot["context"]["north_star"]["goal_text"] == "建立稳定的有效咨询量"
    assert snapshot["context"]["plan_versions"]["sub_goal_id"] == "t3-formal-subgoal"
    assert snapshot["context"]["sub_goal"]["selected_sub_goal"]["id"] == "t3-formal-subgoal"
    assert snapshot["context"]["business_diagnosis"]["version_no"] == 1
    assert snapshot["context"]["plan_versions"]["sub_plan_version_id"] == ids["sub_version_id"]
    assert snapshot["context"]["task_decision_input"]["previous_review_feedback"]["recommendations"] == ["增加真实案例"]
    assert snapshot["id"] == snapshot["context"]["snapshot_id"]

    task_store = ProductionTaskStore(conn)
    task_store.claim_task(task["id"], worker_id="test-worker")
    assert lines.get_line(line["id"])["status"] == "producing"
    topic_id = seed.topic("t3-topic-task-a")
    conn.execute(
        """INSERT INTO contents(id,round_id,topic_id,platform,title,body)
           VALUES('t3-content-a',? ,?,'xhs','单条内容','正文')""",
        (round_id, topic_id),
    )
    task_store.complete_task(task["id"], content_id="t3-content-a")
    assert lines.get_line(line["id"])["status"] == "awaiting_save"
    conn.execute(
        """INSERT INTO drafts(id,content_id,platform,status,saved_at)
           VALUES('t3-draft-a','t3-content-a','xhs','draft_pending',CURRENT_TIMESTAMP)"""
    )
    assert lines.get_line(line["id"])["status"] == "review_pending"
    with pytest.raises(sqlite3.IntegrityError, match="approved before"):
        conn.execute("UPDATE drafts SET publish_status='published' WHERE id='t3-draft-a'")
    conn.execute(
        "UPDATE drafts SET review_status='approved' WHERE id='t3-draft-a'"
    )
    assert lines.get_line(line["id"])["status"] == "awaiting_publish"
    conn.execute(
        "UPDATE drafts SET publish_status='published',published_at=CURRENT_TIMESTAMP WHERE id='t3-draft-a'"
    )
    conn.execute(
        """INSERT INTO metrics(draft_id,content_id,platform,fetch_status,fetched_at,attempt,views,likes,collects,comments)
           VALUES('t3-draft-a','t3-content-a','xhs','success',CURRENT_TIMESTAMP,1,120,10,4,2)"""
    )
    conn.execute(
        """INSERT INTO content_review_events(content_id,draft_id,round_id,event_type,actor,reason,created_at)
           VALUES('t3-content-a','t3-draft-a',?,'approved','test','review evidence',CURRENT_TIMESTAMP)""",
        (round_id,),
    )
    conn.commit()
    assert lines.get_line(line["id"])["status"] == "verification_pending"
    lines.transition(
        line["id"], event_type="publish_verified", actor="work_item_4",
        reason="发布结果已核验",
        evidence={"published_url": "https://example.invalid/post/1", "verification_id": "verify-1"},
    )
    assert lines.get_line(line["id"])["status"] == "published"
    lines.transition(
        line["id"], event_type="closed", actor="work_item_4", reason="发布闭环完成",
        evidence={"verification_id": "verify-1", "review_ref": "content_review_events:1"},
    )
    assert lines.get_line(line["id"])["status"] == "closed"

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE production_context_snapshots SET context_json='{}' WHERE production_task_id=?",
            (task["id"],),
        )



def test_disabled_formal_subgoal_production_plan_blocks_task_creation(conn):
    ids = _setup(conn)
    conn.execute(
        "UPDATE sub_goal_production_plans SET enabled=0 WHERE goal_id='t3-formal-subgoal'"
    )
    conn.commit()
    round_id = _create_round(
        conn, key="round-disabled-subgoal", master_id=ids["master_id"],
        sub_id=ids["sub_id"], sub_version_id=ids["sub_version_id"],
        stage_id=ids["stage_id"], line_version_id=ids["line_version_id"],
    )
    with pytest.raises(ProductionContextError, match="正式子目标生产门禁未通过"):
        ProductionTaskStore(conn).create_task(
            round_id=round_id, master_plan_version_id=ids["master_id"],
            sub_plan_id=ids["sub_id"], sub_plan_version_id=ids["sub_version_id"],
            strategy_step_id=ids["stage_key"], platform="xhs", task_ordinal=1,
            idempotency_key="task-disabled-subgoal-plan",
            input_snapshot={"topic": {"title": "blocked"}},
        )
    assert conn.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 0


def test_only_one_active_content_line_per_tenant_database(conn):
    ids = _setup(conn)
    _round_id, first = _create_task(conn, ids, "task-active-a")
    with pytest.raises(ProductionTaskError, match="上一条内容生产线尚未"):
        _create_task(conn, ids, "task-active-b")
    assert conn.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 1
    line = ContentProductionLineStore(conn).get_for_task(first["id"])
    assert line["status"] == "pending"


def test_missing_confirmed_diagnosis_blocks_task_unless_explicitly_waived(conn):
    ids = _setup(conn)
    conn.execute("UPDATE business_diagnosis_reports SET status='stale'")
    conn.commit()
    round_id = _create_round(
        conn, key="round-waiver", master_id=ids["master_id"],
        sub_id=ids["sub_id"], sub_version_id=ids["sub_version_id"],
        stage_id=ids["stage_id"], line_version_id=ids["line_version_id"],
    )
    task_store = ProductionTaskStore(conn)
    with pytest.raises(ProductionContextError, match="confirmed_business_diagnosis"):
        task_store.create_task(
            round_id=round_id, master_plan_version_id=ids["master_id"],
            sub_plan_id=ids["sub_id"], sub_plan_version_id=ids["sub_version_id"],
            strategy_step_id=ids["stage_key"], platform="xhs", task_ordinal=1,
            idempotency_key="task-without-diagnosis",
            input_snapshot={"topic": {"title": "blocked"}},
        )
    assert conn.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 0

    waiver = ContentProductionLineStore(conn).record_waiver(
        round_id=round_id, platform="xhs",
        waived_items=["confirmed_business_diagnosis"],
        reason="业务负责人确认本轮使用已存档的线下诊断材料",
        actor="business-owner",
        evidence={"document_ref": "offline-diagnosis-2026-11"},
    )
    with pytest.raises(ValueError, match="已有未消费的人工豁免"):
        ContentProductionLineStore(conn).record_waiver(
            round_id=round_id, platform="xhs",
            waived_items=["confirmed_business_diagnosis"],
            reason="重复豁免", actor="business-owner",
            evidence={"document_ref": "duplicate"},
        )
    task = task_store.create_task(
        round_id=round_id, master_plan_version_id=ids["master_id"],
        sub_plan_id=ids["sub_id"], sub_plan_version_id=ids["sub_version_id"],
        strategy_step_id=ids["stage_key"], platform="xhs", task_ordinal=1,
        idempotency_key="task-with-diagnosis-waiver",
        input_snapshot={"topic": {"title": "waived"}},
    )
    line = ContentProductionLineStore(conn).get_for_task(task["id"])
    assert line["context_snapshot"]["context_status"] == "waived"
    assert line["context_snapshot"]["context"]["manual_waiver"]["id"] == waiver["id"]
    assert conn.execute(
        "SELECT consumed_task_id FROM production_context_waivers WHERE id=?", (waiver["id"],)
    ).fetchone()[0] == task["id"]


def test_migration_maps_legacy_tasks_without_rewriting_them(conn):
    """Re-apply migration 150 over a pre-production-line baseline and verify a lossless legacy map."""
    ids = _setup(conn)
    round_id = _create_round(
        conn, key="round-legacy-map", master_id=ids["master_id"],
        sub_id=ids["sub_id"], sub_version_id=ids["sub_version_id"],
        stage_id=ids["stage_id"], line_version_id=ids["line_version_id"],
    )
    conn.commit()
    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    try:
        conn.backup(legacy)
        legacy.execute("PRAGMA foreign_keys=ON")
        new_triggers = [
            "trg_content_production_line_after_task_insert",
            "trg_production_tasks_single_item_ordinal",
            "trg_production_tasks_require_context_before_claim",
            "trg_content_production_line_transition_guard",
            "trg_production_task_status_to_content_line",
            "trg_content_production_line_event_after_status",
            "trg_content_production_line_after_draft_insert",
            "trg_content_production_line_after_draft_review",
            "trg_content_production_line_prevent_unreviewed_publish",
            "trg_content_production_context_snapshots_no_update",
            "trg_content_production_context_snapshots_no_delete",
            "trg_content_production_line_events_no_update",
            "trg_content_production_line_events_no_delete",
            "trg_production_context_waivers_consume_once",
            "trg_production_context_waivers_no_delete",
        ]
        for name in new_triggers:
            legacy.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        for name in (
            "content_production_line_events", "production_context_snapshots",
            "production_context_waivers", "content_production_lines",
        ):
            legacy.execute(f'DROP TABLE IF EXISTS "{name}"')
        now = "2026-11-04T12:00:00+08:00"
        legacy.execute(
            """INSERT INTO production_tasks(
                id,idempotency_key,round_id,master_plan_version_id,sub_plan_id,
                sub_plan_version_id,strategy_step_id,platform,task_ordinal,
                production_line_version_id,status,attempt_count,max_attempts,input_snapshot_json,
                available_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',0,3,?,?,?,?)""",
            (
                "legacy-task-t3", "legacy-idempotency", round_id, ids["master_id"],
                ids["sub_id"], ids["sub_version_id"], ids["stage_key"], "xhs", 1,
                ids["line_version_id"], '{"topic":{"title":"历史任务"}}', now, now, now,
            ),
        )
        legacy.commit()
        migration = Path(__file__).resolve().parents[1] / "migrations" / "150_content_production_lines_and_context.sql"
        legacy.executescript(migration.read_text(encoding="utf-8"))

        line = legacy.execute(
            "SELECT * FROM content_production_lines WHERE production_task_id='legacy-task-t3'"
        ).fetchone()
        snapshot = legacy.execute(
            "SELECT * FROM production_context_snapshots WHERE production_task_id='legacy-task-t3'"
        ).fetchone()
        task = legacy.execute(
            "SELECT status,input_snapshot_json FROM production_tasks WHERE id='legacy-task-t3'"
        ).fetchone()
        assert line["status"] == "legacy_mapped"
        assert line["legacy_task_status"] == "pending"
        assert snapshot["context_status"] == "legacy_incomplete"
        assert json.loads(snapshot["context_json"])["legacy_incomplete"] is True
        assert task["status"] == "pending"
        assert json.loads(task["input_snapshot_json"])["topic"]["title"] == "历史任务"
        assert legacy.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="complete business context snapshot"):
            legacy.execute(
                "UPDATE production_tasks SET status='running' WHERE id='legacy-task-t3'"
            )
    finally:
        legacy.close()
