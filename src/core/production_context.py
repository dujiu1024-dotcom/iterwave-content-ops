"""Fail-closed assembly of the business context frozen for each content task."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class ProductionContextError(ValueError):
    """Raised when required business context cannot be assembled safely."""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _required_table(tables: set[str], name: str) -> None:
    if name not in tables:
        raise ProductionContextError(f"必需经营上下文数据表缺失：{name}")


def _safe_rows(conn, table: str, columns: list[str], where: str = "", args=(), limit: int = 50):
    available = _columns(conn, table)
    selected = [name for name in columns if name in available]
    if not selected:
        raise ProductionContextError(f"经营上下文表 {table} 缺少可读取字段")
    sql = f'SELECT {",".join(selected)} FROM "{table}"'
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY rowid DESC LIMIT ?"
    return [dict(row) for row in conn.execute(sql, [*args, limit]).fetchall()]


def build_production_context_snapshot(
    conn: sqlite3.Connection, *, task_id: str, idempotency_key: str,
    round_id: int, master_plan_version_id: int, sub_plan_id: str,
    sub_plan_version_id: int, strategy_step_id: str, platform: str,
    task_input: dict[str, Any],
) -> dict[str, Any]:
    """Capture all decision inputs; missing required facts block task creation unless waived."""
    tables = _tables(conn)
    required = (
        "goals", "master_plan_versions", "account_group_north_star_versions", "master_plan_business_snapshots",
        "sub_plans", "sub_plan_versions", "sub_plan_stages", "platform_accounts",
        "content_production_lines", "content_production_line_events",
        "production_context_waivers", "business_diagnosis_reports", "contents", "drafts", "metrics",
        "platform_insights", "account_snapshots", "content_review_events", "strategy_effect_reviews",
        "original_assets", "original_asset_versions", "original_asset_links",
        "original_asset_gaps", "sub_goals", "sub_goal_dependencies", "sub_goal_production_plans",
    )
    for table in required:
        _required_table(tables, table)

    round_row = conn.execute(
        """SELECT id,goal_id,master_plan_version_id,sub_plan_id,sub_plan_version_id,
                  sub_plan_stage_id,sub_plan_snapshot,sub_plan_stage_snapshot,
                  production_line_version_id
           FROM spiral_rounds WHERE id=?""", (round_id,)
    ).fetchone()
    if round_row is None:
        raise ProductionContextError("生产轮次不存在")
    round_data = dict(round_row)
    if (
        int(round_data["master_plan_version_id"] or 0) != int(master_plan_version_id)
        or str(round_data["sub_plan_id"] or "") != str(sub_plan_id)
        or int(round_data["sub_plan_version_id"] or 0) != int(sub_plan_version_id)
    ):
        raise ProductionContextError("生产轮次计划归属与任务参数不一致")

    master = conn.execute(
        """SELECT id,version_no,status,plan_signature,plan_snapshot
           FROM master_plan_versions WHERE id=?""", (master_plan_version_id,)
    ).fetchone()
    if master is None or master["status"] != "effective":
        raise ProductionContextError("任务绑定的母计划版本不存在或已失效")
    subplan = conn.execute(
        """SELECT p.id,p.platform,p.title,p.status,p.current_version_id,
                  v.version_no,v.status AS version_status,v.snapshot_json,
                  v.master_plan_version_id
           FROM sub_plans p JOIN sub_plan_versions v ON v.id=?
           WHERE p.id=? AND v.sub_plan_id=p.id""",
        (sub_plan_version_id, sub_plan_id),
    ).fetchone()
    if (
        subplan is None or subplan["status"] != "active"
        or subplan["version_status"] != "effective"
        or int(subplan["current_version_id"] or 0) != int(sub_plan_version_id)
        or str(subplan["platform"]) != str(platform)
    ):
        raise ProductionContextError("当前平台子计划未生效或与任务平台不一致")
    stage = conn.execute(
        """SELECT * FROM sub_plan_stages
           WHERE sub_plan_version_id=? AND stage_key=?""",
        (sub_plan_version_id, strategy_step_id),
    ).fetchone()
    if stage is None:
        raise ProductionContextError("当前子目标或策略步骤不存在")
    stage_data = dict(stage)
    try:
        dependencies = json.loads(stage_data.get("dependencies_json") or "[]")
    except (TypeError, ValueError) as exc:
        raise ProductionContextError("策略步骤依赖数据损坏") from exc
    if not isinstance(dependencies, list):
        raise ProductionContextError("策略步骤依赖数据格式无效")
    stage_data["dependencies"] = dependencies
    if not bool(stage_data.get("entry_condition_met", 1)):
        raise ProductionContextError("当前子目标的进入条件尚未满足")
    # Re-read predecessor outcomes at snapshot time. A dependency key alone is
    # insufficient: the next content must know the predecessor's actual result
    # and evaluation, and a stale/failed predecessor must block production.
    try:
        from .plan_version_store import PlanVersionStore
        ready_stage = PlanVersionStore(conn).assert_stage_ready(
            int(stage_data["id"]), version_id=int(sub_plan_version_id)
        )
    except Exception as exc:
        raise ProductionContextError(f"子目标依赖或验收条件未通过：{exc}") from exc
    stage_data["dependency_outcomes"] = ready_stage.get("dependency_outcomes", {})

    # The formal task-2 subgoal model is the production gate. Legacy strategy-stage
    # dependencies above remain useful context, but cannot substitute for a version-bound
    # business subgoal with an enabled production plan and satisfied predecessor evidence.
    from .subgoal_store import stage_production_goals, assert_goal_ready
    production_goals = stage_production_goals(
        conn, int(sub_plan_version_id), int(stage_data["id"])
    )
    active_production_goals = [
        goal for goal in production_goals
        if goal["status"] not in {"completed", "cancelled"}
    ]
    if not active_production_goals:
        raise ProductionContextError("当前策略阶段没有未完成的正式子目标生产计划")
    # Respect goal_order: do not skip a blocked earlier goal and silently produce a
    # later one. The next goal becomes eligible only after the preceding goal is accepted.
    next_goal = active_production_goals[0]
    try:
        ready_sub_goal = assert_goal_ready(
            conn, str(next_goal["id"]), int(sub_plan_version_id), int(stage_data["id"])
        )
    except Exception as exc:
        raise ProductionContextError(f"正式子目标生产门禁未通过：{exc}") from exc
    stage_data["formal_production_goals"] = production_goals
    stage_data["selected_sub_goal"] = ready_sub_goal

    missing: list[str] = []
    north_star_row = conn.execute(
        """SELECT mv.north_star_version_id,mv.north_star_snapshot_json,
                  mv.north_star_attribution_status,ns.version_no,ns.snapshot_json,
                  ns.status AS north_star_status
           FROM master_plan_versions mv
           LEFT JOIN account_group_north_star_versions ns
             ON ns.id=mv.north_star_version_id
           WHERE mv.id=?""",
        (master_plan_version_id,),
    ).fetchone()
    north_star = None
    if north_star_row is None:
        raise ProductionContextError("母计划版本不存在，无法读取其北极星版本")
    if (
        north_star_row["north_star_attribution_status"] != "attributed"
        or north_star_row["north_star_version_id"] is None
        or north_star_row["snapshot_json"] is None
    ):
        missing.append("north_star")
    else:
        try:
            north_star_snapshot = json.loads(
                north_star_row["north_star_snapshot_json"] or north_star_row["snapshot_json"]
            )
        except (TypeError, ValueError) as exc:
            raise ProductionContextError("母计划绑定的北极星快照损坏") from exc
        if not isinstance(north_star_snapshot, dict):
            raise ProductionContextError("母计划绑定的北极星快照格式无效")
        north_star = {
            "id": int(north_star_row["north_star_version_id"]),
            "version_no": int(north_star_row["version_no"]),
            **north_star_snapshot,
        }

    business_snapshot = conn.execute(
        """SELECT business_profile_version_no,snapshot_json,captured_at
           FROM master_plan_business_snapshots WHERE master_plan_version_id=?""",
        (master_plan_version_id,),
    ).fetchone()
    if business_snapshot is None:
        missing.append("business_profile_snapshot")

    diagnosis = conn.execute(
        """SELECT id,version_no,report_json,source_snapshot_json,source_fingerprint,
                  confirmed_at,confirmed_by,created_at
           FROM business_diagnosis_reports WHERE status='confirmed'
           ORDER BY version_no DESC,id DESC LIMIT 1"""
    ).fetchone()
    if diagnosis is None:
        missing.append("confirmed_business_diagnosis")

    waiver = conn.execute(
        """SELECT * FROM production_context_waivers
           WHERE round_id=? AND platform=? AND consumed_task_id IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (round_id, platform),
    ).fetchone() if "production_context_waivers" in tables else None
    waiver_data = dict(waiver) if waiver is not None else None
    waived_items: set[str] = set()
    if waiver_data:
        try:
            waived_items = set(json.loads(waiver_data["waived_items_json"]))
        except (TypeError, ValueError):
            raise ProductionContextError("人工豁免记录损坏")
    business_snapshot_data = dict(business_snapshot) if business_snapshot else None
    if business_snapshot_data:
        business_snapshot_data["snapshot"] = json.loads(business_snapshot_data.pop("snapshot_json"))
        frozen_business = business_snapshot_data["snapshot"]
        profile = frozen_business.get("profile") if isinstance(frozen_business, dict) else {}
        catalog = frozen_business.get("items", []) if isinstance(frozen_business, dict) else []
        has_profile_facts = isinstance(profile, dict) and any(
            str(profile.get(key) or "").strip()
            for key in ("business_name", "introduction", "industry", "business_model", "business_stage", "operating_goal")
        )
        has_offer = isinstance(catalog, list) and any(
            isinstance(item, dict) and item.get("entity_type") in ("product", "service")
            for item in catalog
        )
        if not has_profile_facts or not has_offer:
            if "business_profile_snapshot" not in missing:
                missing.append("business_profile_snapshot")
    diagnosis_data = dict(diagnosis) if diagnosis else None
    if diagnosis_data:
        diagnosis_data["report"] = json.loads(diagnosis_data.pop("report_json"))
        diagnosis_data["source_snapshot"] = json.loads(diagnosis_data.pop("source_snapshot_json"))

    # The most recent confirmed publication is a fact only when the draft records it.
    published = conn.execute(
        """SELECT c.id,c.platform,c.title,c.body,c.round_id,d.id AS draft_id,
                  d.publish_status,d.published_at,d.user_confirmed_at,d.publish_time_source,
                  l.status AS production_line_status,
                  (SELECT e.evidence_json FROM content_production_line_events e
                   WHERE e.content_production_line_id=l.id AND e.event_type='publish_verified'
                   ORDER BY e.id DESC LIMIT 1) AS publish_verification_json
           FROM contents c JOIN drafts d ON d.content_id=c.id
           LEFT JOIN content_production_lines l ON l.content_id=c.id
           WHERE c.platform=? AND d.publish_status='published'
           ORDER BY COALESCE(d.published_at,d.user_confirmed_at) DESC,d.id DESC
           LIMIT 1""", (platform,)
    ).fetchone()
    published_data = dict(published) if published else None
    if published_data:
        verification_raw = published_data.pop("publish_verification_json", None)
        if verification_raw:
            try:
                published_data["publish_verification"] = json.loads(verification_raw)
            except (TypeError, ValueError) as exc:
                raise ProductionContextError("最近发布核验证据损坏") from exc
            published_data["verification_status"] = "verified"
        else:
            published_data["publish_verification"] = None
            published_data["verification_status"] = "legacy_unverified"
    latest_review_ref = None
    if published_data:
        metrics = _safe_rows(
            conn, "metrics",
            ["id","content_id","fetched_at","fetch_status","views","likes","collects",
             "comments","shares","new_followers"],
            "content_id=? AND fetch_status='success'", (published_data["id"],), 10,
        )
        reviews = _safe_rows(
            conn, "content_review_events",
            ["id","content_id","draft_id","event_type","actor","reason","created_at"],
            "content_id=?", (published_data["id"],), 10,
        )
        published_data["metrics"] = metrics
        published_data["review_events"] = reviews
        published_data["metrics_status"] = "available" if metrics else "not_collected"
        if reviews:
            latest_review_ref = f"content_review_events:{reviews[0]['id']}"

    registered_accounts = _safe_rows(
        conn, "platform_accounts",
        ["id","platform","account_handle","display_name","status","notes","created_at","updated_at"],
        "status='active'", (), 20,
    )
    current_platform_account = next(
        (account for account in registered_accounts if account["platform"] == platform), None
    )
    platform_performance = _safe_rows(
        conn, "platform_insights",
        ["id","round_id","platform","analyzed_at","days_analyzed","trend","best_hours",
         "best_title_style","best_card_style","top_dimensions","sample_count",
         "avg_interaction_rate","conclusion"],
        "platform=?", (platform,), 30,
    )
    for insight in platform_performance:
        for key in ("trend","best_hours","best_title_style","best_card_style","top_dimensions"):
            if insight.get(key):
                try:
                    insight[key] = json.loads(insight[key])
                except (TypeError, ValueError) as exc:
                    raise ProductionContextError(f"平台分析字段 {key} 数据损坏") from exc
    account_snapshots = _safe_rows(
        conn, "account_snapshots",
        ["id","platform","followers","likes_total","captured_at","source"],
        "platform=?", (platform,), 30,
    )
    strategy_reviews = _safe_rows(
        conn, "strategy_effect_reviews",
        ["id","stage_id","assessment","recommendation","evidence_note","confidence",
         "evidence_json","created_by","created_at"],
        "stage_id=?", (int(stage_data["id"]),), 10,
    )
    for review in strategy_reviews:
        try:
            review["evidence"] = json.loads(review.pop("evidence_json", "{}") or "{}")
        except (TypeError, ValueError) as exc:
            raise ProductionContextError("策略复盘证据数据损坏") from exc
    if not published_data and strategy_reviews and latest_review_ref is None:
        latest_review_ref = f"strategy_effect_reviews:{strategy_reviews[0]['id']}"
    if published_data and (
        not published_data.get("metrics")
        or not latest_review_ref
        or (
            published_data.get("production_line_status") in ("verification_pending", "published", "closed")
            and published_data.get("verification_status") != "verified"
        )
    ):
        if "recent_published_content" not in missing:
            missing.append("recent_published_content")

    if missing and (not waiver_data or not set(missing).issubset(waived_items)):
        raise ProductionContextError(
            "必需经营上下文缺失，已阻止生产：" + "、".join(missing)
            + "。如确需继续，必须先提交有原因和证据的人工豁免。"
        )

    assets = _safe_rows(
        conn, "original_assets",
        ["id","asset_type","title","description","source_kind","source_media_asset_id",
         "source_content_asset_id","source_kb_id","source_uri","source_version",
         "current_version_no","readability_status","completeness_status","duplicate_status",
         "freshness_status","commercial_use_status","privacy_status","review_status",
         "metadata_json","updated_at"],
        "review_status='confirmed' AND archived_at IS NULL", (), 100,
    )
    for asset in assets:
        try:
            asset["metadata"] = json.loads(asset.pop("metadata_json") or "{}")
        except (TypeError, ValueError) as exc:
            raise ProductionContextError("原始素材元数据损坏") from exc
    asset_ids = [int(asset["id"]) for asset in assets if asset.get("id") is not None]
    asset_versions = []
    asset_relationships = []
    if asset_ids:
        placeholders = ",".join("?" for _ in asset_ids)
        asset_versions = _safe_rows(
            conn, "original_asset_versions",
            ["id","original_asset_id","version_no","source_version","title","description",
             "metadata_json","review_status","commercial_use_status","created_at"],
            f"original_asset_id IN ({placeholders})", tuple(asset_ids), 200,
        )
        for version in asset_versions:
            try:
                version["metadata"] = json.loads(version.pop("metadata_json") or "{}")
            except (TypeError, ValueError) as exc:
                raise ProductionContextError("原始素材版本元数据损坏") from exc
        asset_relationships = _safe_rows(
            conn, "original_asset_links",
            ["id","original_asset_id","entity_type","entity_id","relation_type","created_at"],
            f"original_asset_id IN ({placeholders})", tuple(asset_ids), 300,
        )
    gaps = _safe_rows(
        conn, "original_asset_gaps",
        ["id","entity_type","entity_id","asset_type","title","reason","priority","status",
         "source_plan_id","resolved_asset_id","created_at","updated_at"],
        "status IN ('open','in_progress')", (), 100,
    )
    history = _safe_rows(
        conn, "contents",
        ["id","platform","title","body","round_id","created_at","production_task_id"],
        "platform=?", (platform,), 10,
    )
    for historical_content in history:
        historical_content["body_excerpt"] = str(historical_content.pop("body", "") or "")[:1200]
    internal_materials = [a for a in assets if a["source_kind"] in ("kb_source","content_asset","media_asset")]
    external_references = [a for a in assets if a["source_kind"] == "external"]

    diagnosis_report = (diagnosis_data or {}).get("report")
    raw_business_gaps = (
        diagnosis_report.get("business_gaps", diagnosis_report.get("gaps", []))
        if isinstance(diagnosis_report, dict) else []
    )
    business_gaps = (
        raw_business_gaps if isinstance(raw_business_gaps, list)
        else [raw_business_gaps] if isinstance(raw_business_gaps, dict) else []
    )

    context = {
        "schema_version": 1,
        "snapshot_id": "pcs-" + uuid.uuid4().hex,
        "production_task_id": task_id,
        "round_id": int(round_id),
        "account_group": {
            "scope": "isolated_tenant_database",
            "registered_platform_accounts": registered_accounts,
            "current_platform_account": current_platform_account,
            "platform": platform,
        },
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan_versions": {
            "master_plan_version_id": int(master_plan_version_id),
            "master_plan_version_no": int(master["version_no"]),
            "sub_plan_id": sub_plan_id,
            "sub_plan_version_id": int(sub_plan_version_id),
            "sub_plan_version_no": int(subplan["version_no"]),
            "strategy_step_id": strategy_step_id,
            "strategy_step_id_db": int(stage_data["id"]),
            "sub_goal_id": str(ready_sub_goal["id"]),
            "production_line_version_id": round_data["production_line_version_id"],
        },
        "north_star": dict(north_star) if north_star else None,
        "business_profile_snapshot": business_snapshot_data,
        "master_plan_snapshot": json.loads(master["plan_snapshot"]),
        "platform_subplan_snapshot": json.loads(subplan["snapshot_json"]),
        "sub_goal": stage_data,
        "recent_published_content": published_data,
        "recent_historical_content": history,
        "account_performance": {
            "platform_insights": platform_performance,
            "account_snapshots": account_snapshots,
        },
        "strategy_effect_reviews": strategy_reviews,
        "business_diagnosis": diagnosis_data,
        "business_gaps": business_gaps,
        "available_original_assets": assets,
        "original_asset_versions": asset_versions,
        "original_asset_relationships": asset_relationships,
        "resource_references": [
            {"original_asset_id": asset["id"], "version_no": asset.get("current_version_no")}
            for asset in assets
        ],
        "selected_resource_references": task_input.get("original_assets", [])
            if isinstance(task_input, dict) else [],
        "internal_materials": internal_materials,
        "external_references": external_references,
        "resource_gaps": gaps,
        "task_decision_input": task_input,
        "latest_review_ref": latest_review_ref,
        "missing_items": missing,
        "manual_waiver": ({
            "id": waiver_data["id"],
            "waived_items": sorted(waived_items),
            "reason": waiver_data["reason"],
            "actor": waiver_data["actor"],
            "evidence": json.loads(waiver_data["evidence_json"] or "{}"),
            "created_at": waiver_data["created_at"],
        } if waiver_data else None),
    }
    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "context": context,
        "captured_at": context["captured_at"],
        "context_status": "waived" if missing else "complete",
        "waiver": waiver_data,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
