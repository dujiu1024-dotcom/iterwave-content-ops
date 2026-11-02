"""队列任务执行快照：固定非敏感配置指纹、母计划版本和显式执行参数。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


_SENSITIVE_KEYS = {
    "api_key", "apikey", "secret", "client_secret", "password", "cookie", "cookies",
    "authorization", "access_token", "refresh_token", "auth_token", "session_token",
    "private_key", "credential", "credentials",
}


class StaleTaskSnapshot(RuntimeError):
    """排队后非敏感配置或母计划发生变化，拒绝使用新配置静默执行。"""

    safe_for_user = True


def _safe_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return not (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_api_key")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_cookie")
        or normalized.endswith("_token")
    )


def sanitize_snapshot(value: Any) -> Any:
    """递归移除凭据字段；不复制 API Key、Cookie、密码或会话令牌。"""
    if isinstance(value, dict):
        return {
            str(key): sanitize_snapshot(item)
            for key, item in value.items()
            if _safe_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_snapshot(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# 只固定会改变本轮选题、内容生成或平台执行结果的配置。
# 运维/观测/熔断/通知/调度等无关配置调整，不应取消已排队的生产任务。
_PRODUCTION_CONFIG_FIELDS = (
    "topic_kb", "platforms", "scoring", "ultimate_goal", "llm",
    "copy_generation", "media_generation", "media_quality", "vision_ranking",
    "spiral", "plan_suggestion", "timeouts",
)


def config_fingerprint(cfg) -> str:
    raw = cfg.model_dump(mode="json")
    production = {key: raw.get(key) for key in _PRODUCTION_CONFIG_FIELDS}
    safe = sanitize_snapshot(production)
    canonical = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_execution_snapshot(cfg, conn, *, round_id: int, parameters: dict[str, Any]) -> dict[str, Any]:
    from .plan_version import PlanVersionManager

    plan, signature, updated_at, source = PlanVersionManager(cfg, conn).load_plan()
    round_row = conn.execute(
        """SELECT plan_signature,master_plan_version_id,sub_plan_id,sub_plan_version_id,
                  sub_plan_stage_id,sub_plan_snapshot,sub_plan_stage_snapshot
           FROM spiral_rounds WHERE id = ?""", (round_id,),
    ).fetchone()
    round_signature = round_row["plan_signature"] if round_row else None
    round_plan = None
    if round_row and round_row["sub_plan_id"]:
        master_row = conn.execute(
            "SELECT plan_signature,plan_snapshot FROM master_plan_versions WHERE id=?",
            (round_row["master_plan_version_id"],),
        ).fetchone()
        if master_row is None or master_row["plan_signature"] != round_signature:
            raise StaleTaskSnapshot("轮次绑定的主计划版本缺失或签名不一致")
        try:
            round_plan = json.loads(master_row["plan_snapshot"])
        except (TypeError, ValueError):
            raise StaleTaskSnapshot("轮次绑定的主计划快照损坏")
    elif round_signature:
        row = conn.execute(
            "SELECT plan_snapshot FROM signature_history WHERE round_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (round_id,),
        ).fetchone()
        if row:
            try:
                round_plan = json.loads(row["plan_snapshot"])
            except (TypeError, ValueError):
                round_plan = None

    # 未保存母计划时，轮次签名代表创建该轮时的显式参数（选题数/平台等）。
    # load_plan(source='derived') 是默认推导值，不是该轮的计划，不能拿它覆盖/否定轮次快照。
    if round_row and round_row["sub_plan_id"]:
        effective_signature = round_signature
        source = "subplan_version"
    elif source == "derived" and round_signature:
        effective_signature = round_signature
    else:
        if round_signature and round_signature != signature:
            raise StaleTaskSnapshot("目标轮次已基于旧母计划；请先确认并更新轮次，再重新提交生产任务")
        effective_signature = signature

    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_fingerprint": config_fingerprint(cfg),
        "plan_signature": effective_signature,
        "plan_updated_at": updated_at,
        "plan_source": source,
        "round_plan_signature": round_signature,
        "round_plan_snapshot": sanitize_snapshot(round_plan) if round_plan else None,
        "round_binding": ({
            "master_plan_version_id": round_row["master_plan_version_id"],
            "sub_plan_id": round_row["sub_plan_id"],
            "sub_plan_version_id": round_row["sub_plan_version_id"],
            "sub_plan_stage_id": round_row["sub_plan_stage_id"],
            "sub_plan_snapshot_sha256": hashlib.sha256(
                str(round_row["sub_plan_snapshot"] or "").encode("utf-8")
            ).hexdigest(),
            "sub_plan_stage_snapshot_sha256": hashlib.sha256(
                str(round_row["sub_plan_stage_snapshot"] or "").encode("utf-8")
            ).hexdigest(),
        } if round_row and round_row["sub_plan_id"] else None),
        "parameters": sanitize_snapshot(parameters),
    }


def validate_execution_snapshot(snapshot: dict[str, Any], cfg, conn) -> None:
    """配置或母计划发生变化时 fail closed，要求用户确认后重新入队。"""
    if int(snapshot.get("schema_version") or 0) != 1:
        raise StaleTaskSnapshot("任务执行快照版本不受支持，请重新提交任务")
    expected_config = str(snapshot.get("config_fingerprint") or "")
    if not expected_config or config_fingerprint(cfg) != expected_config:
        raise StaleTaskSnapshot("任务入队后账号组配置已变化；为避免使用新配置误执行，任务已停止，请确认配置后重新提交")
    from .subplan_production import production_paused
    if production_paused(conn):
        raise StaleTaskSnapshot("内容生产已暂停，队列任务拒绝执行")
    binding = snapshot.get("round_binding")
    if binding is not None:
        round_id = (snapshot.get("parameters") or {}).get("round_id")
        row = conn.execute(
            """SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,
                      sub_plan_stage_id,sub_plan_snapshot,sub_plan_stage_snapshot
               FROM spiral_rounds WHERE id=?""", (round_id,),
        ).fetchone()
        expected = (
            binding.get("master_plan_version_id"), binding.get("sub_plan_id"),
            binding.get("sub_plan_version_id"), binding.get("sub_plan_stage_id"),
            binding.get("sub_plan_snapshot_sha256"), binding.get("sub_plan_stage_snapshot_sha256"),
        )
        actual = None
        if row is not None:
            actual = (
                row["master_plan_version_id"], row["sub_plan_id"],
                row["sub_plan_version_id"], row["sub_plan_stage_id"],
                hashlib.sha256(str(row["sub_plan_snapshot"] or "").encode("utf-8")).hexdigest(),
                hashlib.sha256(str(row["sub_plan_stage_snapshot"] or "").encode("utf-8")).hexdigest(),
            )
        if actual != expected or not all(expected):
            raise StaleTaskSnapshot("轮次绑定的子计划版本/阶段快照已变化，拒绝执行")
        identity = conn.execute(
            "SELECT status,platform FROM sub_plans WHERE id=?", (binding.get("sub_plan_id"),),
        ).fetchone()
        stage = conn.execute(
            """SELECT s.status,s.start_date,s.end_date,s.target_count,
                      COALESCE(stats.confirmed_published_count,0) AS confirmed_published_count
               FROM sub_plan_stages s
               LEFT JOIN sub_plan_stage_publish_stats stats ON stats.stage_id=s.id
               WHERE s.id=? AND s.sub_plan_version_id=?""",
            (binding.get("sub_plan_stage_id"), binding.get("sub_plan_version_id")),
        ).fetchone()
        if identity is None or identity["status"] != "active":
            raise StaleTaskSnapshot("子计划已暂停或撤销授权，拒绝执行")
        if stage is None or stage["status"] in ("paused", "completed", "cancelled"):
            raise StaleTaskSnapshot("子计划阶段已暂停或完成，拒绝执行")
        if int(stage["confirmed_published_count"] or 0) >= int(stage["target_count"]):
            raise StaleTaskSnapshot("阶段用户确认发布目标已达成，队列任务拒绝执行")
        from .timezone import business_now
        business_date = business_now(cfg).date().isoformat()
        if stage["start_date"] and business_date < stage["start_date"]:
            raise StaleTaskSnapshot("子计划阶段尚未到执行日期，队列任务拒绝执行")
        if stage["end_date"] and business_date > stage["end_date"]:
            raise StaleTaskSnapshot("子计划阶段执行窗口已结束，队列任务拒绝执行")
        broken = conn.execute(
            "SELECT 1 FROM circuit_breaker_events WHERE platform=? AND resolved_at IS NULL LIMIT 1",
            (identity["platform"],),
        ).fetchone()
        if broken:
            raise StaleTaskSnapshot("子计划所属平台已熔断，队列任务拒绝执行")
    expected_plan = str(snapshot.get("plan_signature") or "")
    # derived 轮次以创建时持久化的 round_plan_snapshot 为准；当前推导母计划会随最近轮次变化，
    # 不是可用于验证旧轮次的稳定版本。保存过的正式母计划仍必须严格比对。
    if expected_plan and binding is None:
        from .plan_version import PlanVersionManager
        _, current_signature, _, current_source = PlanVersionManager(cfg, conn).load_plan()
        # 仅当入队和执行时都没有正式母计划，才忽略动态推导签名；
        # 入队后若用户保存了正式母计划，仍应按版本变化拒绝旧任务。
        if current_source == "plan_settings" and current_signature != expected_plan:
            raise StaleTaskSnapshot("任务入队后母计划已变化；为避免按错误计划生产，任务已停止，请确认计划后重新提交")

