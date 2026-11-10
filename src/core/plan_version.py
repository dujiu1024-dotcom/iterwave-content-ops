"""
母计划版本签名（§19，R18）—— 改母计划自动使下游失效。

签名覆盖 4 个关键字段（其余字段改动不视为「换计划」）：
    topic_count_per_platform / dimension_allocation / enabled_platforms / content_format

落库位置：
- spiral_rounds.plan_signature（轮次级）
- signature_history（持久化审计，重启不丢）

用法：
    from src.core.plan_version import PlanVersionManager
    pvm = PlanVersionManager(cfg, conn)
    plan = pvm.build_plan(topic_count=5, platforms=[p.value for p in platforms],
                          dimension_allocation={"AI教育": 3, "效率工具": 2})
    pvm.record(round_id, plan)          # 写 rounds.plan_signature + signature_history
    pvm.invalidate_changed_plans()      # 找出与当前计划不一致的活跃轮次
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from .timezone import business_now
from typing import Optional

logger = logging.getLogger(__name__)

_SIGNATURE_FIELDS = (
    "topic_count_per_platform",
    "dimension_allocation",
    "enabled_platforms",
    "content_format",
)

_ACTIVE_STATUSES = ("created", "producing", "awaiting_publish")


def legacy_signature_with_platform_counts(plan: dict) -> str:
    """升级兼容：167 之前的签名把平台分项篇数并入 hash。

    只用于校验历史母计划草稿/生效版本的完整性，新代码不再生成该签名。
    """
    payload = {k: plan.get(k) for k in _SIGNATURE_FIELDS}
    counts = {
        p: c["topic_count"]
        for p, c in (plan.get("per_platform") or {}).items()
        if c.get("topic_count")
    }
    if counts:
        payload["platform_topic_counts"] = counts
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlanVersionManager:
    """母计划签名计算、持久化与下游失效标记。"""

    def __init__(self, cfg, conn):
        self.cfg = cfg
        self.conn = conn

    # ------------------------------------------------------------------
    # 计划构造与签名
    # ------------------------------------------------------------------

    def build_plan(
        self,
        *,
        topic_count_per_platform: int,
        platforms: list[str],
        dimension_allocation: Optional[dict[str, int]] = None,
        content_format: str = "image_text",
        per_platform: Optional[dict[str, dict]] = None,
        cadence: Optional[dict] = None,
    ) -> dict:
        """母计划 v2。

        - dimension_allocation：维度配额（本轮每个维度至少/至多选几条），总和不能超过每平台选题数；
          余下名额按评分补齐
        - per_platform：平台分项计划 {平台: {topic_count: 本平台篇数（≤ 母计划选题数）,
          publish_slots: ["20:00", ...] 自动排期用的发布时段}}
        - cadence：节奏 {round_interval_days: 两轮间隔天数, rounds_planned: 本阶段计划轮数,
          goal_id: 关联目标}；自动闭环按它决定何时开下一轮、开到第几轮停
        """
        plan = {
            "topic_count_per_platform": int(topic_count_per_platform),
            "dimension_allocation": dict(sorted((dimension_allocation or {}).items())),
            "enabled_platforms": sorted(platforms),
            "content_format": content_format,
        }
        pp = {}
        for p, conf in sorted((per_platform or {}).items()):
            if p not in plan["enabled_platforms"] or not isinstance(conf, dict):
                continue
            entry: dict = {}
            tc = conf.get("topic_count")
            if tc not in (None, ""):
                entry["topic_count"] = max(1, min(int(tc), plan["topic_count_per_platform"]))
            slots = sorted({str(x).strip() for x in conf.get("publish_slots") or [] if str(x).strip()})
            if slots:
                entry["publish_slots"] = slots
            if entry:
                pp[p] = entry
        if pp:
            plan["per_platform"] = pp
        cad = {k: v for k, v in (cadence or {}).items()
               if k in ("round_interval_days", "rounds_planned", "goal_id") and v not in (None, "")}
        if cad:
            plan["cadence"] = cad
        return plan

    @staticmethod
    def platform_topic_count(plan: dict, platform: str) -> int:
        base = int(plan.get("topic_count_per_platform") or 5)
        return int((plan.get("per_platform") or {}).get(platform, {}).get("topic_count") or base)

    def compute_signature(self, plan: dict) -> str:
        """只对战略字段做规范化哈希。

        平台分项篇数/发布时段属于平台执行配置，不参与母计划签名：
        改一个平台的产能不应让其他平台的子计划一起失效（D3）。
        """
        payload = {k: plan.get(k) for k in _SIGNATURE_FIELDS}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def split_runtime_config(plan: dict) -> tuple[dict, dict]:
        """把母计划拆成「战略快照」和「平台执行配置」。

        战略快照写入 master_plan_versions.plan_snapshot（不可变、参与签名）；
        平台执行配置写入 platform_plan_configs / master_plan_runtime_configs。
        """
        strategic = {
            key: value for key, value in plan.items()
            if key not in ("per_platform", "cadence")
        }
        runtime: dict = {}
        per_platform = plan.get("per_platform")
        if isinstance(per_platform, dict) and per_platform:
            runtime["per_platform"] = {
                str(p): dict(conf) for p, conf in per_platform.items() if isinstance(conf, dict)
            }
        cadence = plan.get("cadence")
        if isinstance(cadence, dict) and cadence:
            runtime["cadence"] = dict(cadence)
        return strategic, runtime

    @staticmethod
    def platform_config_diff(previous: dict, current: dict) -> list[str]:
        """返回平台执行配置发生变化的平台列表（只影响这些平台的子计划）。"""
        prev_pp = previous.get("per_platform") or {}
        next_pp = current.get("per_platform") or {}
        changed: list[str] = []
        for platform in sorted(set(prev_pp) | set(next_pp)):
            if prev_pp.get(platform) != next_pp.get(platform):
                changed.append(platform)
        return changed

    def current_plan(self, topic_count_per_platform: Optional[int] = None) -> dict:
        """推导「冷启动」母计划：选题数取最近一轮实际执行的选题条数，缺省 5。

        仅在账号组尚无生效母计划版本时使用（source='derived'），不可作为
        生产轮次的计划来源；生产轮次必须绑定生效版本。
        """
        if topic_count_per_platform is None:
            row = self.conn.execute(
                "SELECT topic_ids FROM spiral_rounds ORDER BY id DESC LIMIT 1"
            ).fetchone()
            try:
                n = len(json.loads(row["topic_ids"])) if row and row["topic_ids"] else 0
            except (TypeError, ValueError):
                n = 0
            topic_count_per_platform = n or self.cfg.plan_suggestion.default_topic_count
        return self.build_plan(
            topic_count_per_platform=topic_count_per_platform,
            platforms=self.cfg.platforms.enabled_keys(),
        )

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------

    def record(self, round_id: int, plan: dict, author: str = "system") -> str:
        """写入轮次签名；已绑定母计划版本时，以不可变版本快照/签名为唯一权威源。"""
        round_row = self.conn.execute(
            "SELECT master_plan_version_id FROM spiral_rounds WHERE id = ?",
            (round_id,),
        ).fetchone()
        history_plan = plan
        signature = self.compute_signature(plan)
        master_version_id = (
            round_row["master_plan_version_id"]
            if round_row is not None and "master_plan_version_id" in round_row.keys()
            else None
        )
        if master_version_id is not None:
            master_row = self.conn.execute(
                "SELECT plan_signature, plan_snapshot FROM master_plan_versions WHERE id = ?",
                (master_version_id,),
            ).fetchone()
            if master_row is None:
                raise ValueError("轮次绑定的母计划版本不存在，拒绝记录轮次签名")
            try:
                master_snapshot = json.loads(master_row["plan_snapshot"])
            except (TypeError, ValueError) as exc:
                raise ValueError("轮次绑定的母计划快照损坏，拒绝记录轮次签名") from exc
            if not isinstance(master_snapshot, dict):
                raise ValueError("轮次绑定的母计划快照格式无效，拒绝记录轮次签名")
            authoritative_signature = self.compute_signature(master_snapshot)
            stored_signature = str(master_row["plan_signature"] or "")
            if authoritative_signature != stored_signature:
                # 兼容历史版本：旧签名可能包含平台分项篇数。
                if legacy_signature_with_platform_counts(master_snapshot) != stored_signature:
                    raise ValueError("母计划快照与其签名不一致，拒绝创建轮次")
            signature = stored_signature
            history_plan = master_snapshot

        self.conn.execute(
            "UPDATE spiral_rounds SET plan_signature = ? WHERE id = ?",
            (signature, round_id),
        )
        self.conn.execute(
            "INSERT INTO signature_history (plan_signature, plan_snapshot, round_id, created_at, author) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                signature,
                json.dumps(history_plan, ensure_ascii=False, sort_keys=True),
                round_id,
                business_now(self.cfg).strftime("%Y-%m-%d %H:%M:%S"),
                author,
            ),
        )
        self.conn.commit()
        logger.info("📌 轮次 %s 母计划签名已记录: %s", round_id, signature[:12])
        return signature

    # ------------------------------------------------------------------
    # 母计划读写（G-09 总计划 tab）
    # ------------------------------------------------------------------

    def load_plan(self) -> tuple[dict, str, Optional[str], str]:
        """读当前母计划。返回 (plan, signature, updated_at, source)。

        source = 'plan_settings'（用户在 UI 保存过）或 'derived'（从最近一轮推导）。
        """
        if self._has_plan_settings():
            row = self.conn.execute(
                "SELECT plan_snapshot, plan_signature, updated_at FROM plan_settings WHERE id = 1"
            ).fetchone()
            if row:
                try:
                    plan = json.loads(row["plan_snapshot"])
                except (TypeError, ValueError):
                    plan = self.current_plan()
                else:
                    return plan, row["plan_signature"], row["updated_at"], "plan_settings"
        plan = self.current_plan()
        return plan, self.compute_signature(plan), None, "derived"

    def save_plan(self, plan: dict, author: str = "user") -> dict:
        """已停用：母计划必须走不可变版本 + 审核生效，禁止直写投影（P1-3）。"""
        raise RuntimeError(
            "save_plan 已停用：母计划必须通过 "
            "PlanVersionStore.create_master_draft + activate_master_version 落库"
        )

    def _has_plan_settings(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='plan_settings'"
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 下游失效（R18）
    # ------------------------------------------------------------------

    def invalidate_changed_plans(self, current_plan: Optional[dict] = None) -> dict:
        """把签名与当前计划不一致的活跃轮次标记为「建议重存」。

        只做标记（drafts.error_msg + rounds.transition_log 追加），不删除用户内容。
        """
        plan = current_plan or self.load_plan()[0]
        current_sig = self.compute_signature(plan)

        placeholders = ",".join("?" for _ in _ACTIVE_STATUSES)
        # 已绑定母计划版本/子计划的轮次由版本机制处理，这里只处理历史未绑定轮次。
        bound_filter = ""
        round_columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(spiral_rounds)").fetchall()
        }
        if "master_plan_version_id" in round_columns:
            bound_filter = " AND master_plan_version_id IS NULL"
        rows = self.conn.execute(
            f"SELECT id, status, plan_signature, transition_log FROM spiral_rounds "
            f"WHERE status IN ({placeholders}) AND plan_signature IS NOT NULL "
            f"AND sub_plan_id IS NULL{bound_filter}",
            _ACTIVE_STATUSES,
        ).fetchall()

        stale: list[int] = []
        for row in rows:
            if row["plan_signature"] == current_sig:
                continue
            stale.append(row["id"])
            self._append_transition(row["id"], row["transition_log"],
                                    "母计划变更，本轮产物已标记为基于旧计划")
            self.conn.execute(
                "UPDATE drafts SET error_msg = ? "
                "WHERE status IN ('draft_pending','draft_saved') AND content_id IN "
                "(SELECT id FROM contents WHERE round_id = ?)",
                ("草稿内容基于旧计划，建议按新计划重存", row["id"]),
            )

        if stale:
            self.conn.commit()
            logger.warning("⚠ 母计划签名 %s 与 %d 个活跃轮次不一致: %s",
                           current_sig[:12], len(stale), stale)

        return {"current_signature": current_sig, "stale_rounds": stale}

    def _append_transition(self, round_id: int, raw: Optional[str], reason: str) -> None:
        try:
            log = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            log = []
        log.append({
            "from": None,
            "to": None,
            "at": business_now(self.cfg).isoformat(timespec="seconds"),
            "reason": reason,
        })
        self.conn.execute(
            "UPDATE spiral_rounds SET transition_log = ? WHERE id = ?",
            (json.dumps(log, ensure_ascii=False), round_id),
        )


# =====================================================================
# 阶段目标拆分（§22 ⑥）：目标 → 母计划节奏建议
# =====================================================================

def suggest_plan_from_goal(cfg, conn, goal_id: str, current_plan: Optional[dict] = None) -> dict:
    """把一个阶段目标拆成「每平台篇数 × 计划轮数 × 轮间隔」。只给建议，不落库；
    用户在总计划里确认后保存才生效（避免系统替用户承诺产能）。"""
    import math
    from .goal_progress import METRIC_COLUMNS, PERCENT_METRICS, GoalProgressUpdater

    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise LookupError(f"目标 {goal_id} 不存在")
    g = dict(row)
    plan = dict(current_plan or {})
    platforms = [g["target_platform"]] if g.get("target_platform") else (
        plan.get("enabled_platforms") or cfg.platforms.enabled_keys())
    policy = cfg.plan_suggestion
    topic_count = int(plan.get("topic_count_per_platform") or policy.default_topic_count)
    notes: list[str] = []

    today = business_now(cfg).date()
    period_days = policy.default_period_days
    if g.get("metric_period_end"):
        try:
            end = datetime.fromisoformat(str(g["metric_period_end"])[:10]).date()
            period_days = max(1, (end - today).days)
        except ValueError:
            pass
    else:
        notes.append(f"目标没有截止日期，按 {policy.default_period_days} 天规划")

    metric = g.get("target_metric")
    col = METRIC_COLUMNS.get(metric)
    contents_needed = None
    if col and col[1] == "sum":
        from .timezone import business_date_cutoffs, timestamp_since_sql, timestamp_before_exclusive_sql
        where = [
            "metric_key = ?", "value IS NOT NULL",
            "collection_status IN ('succeeded','partial')",
        ]
        params = [col[0]]
        if g.get("target_platform"):
            where.append("platform = ?")
            params.append(g["target_platform"])
        period_valid = True
        try:
            if g.get("metric_period_start"):
                start_utc, start_legacy = business_date_cutoffs(
                    str(g["metric_period_start"])[:10], cfg,
                )
                where.append(timestamp_since_sql("measured_at"))
                params.extend((start_utc, start_legacy))
            if g.get("metric_period_end"):
                from datetime import date, timedelta
                end_exclusive = date.fromisoformat(str(g["metric_period_end"])[:10]) + timedelta(days=1)
                end_utc, end_legacy = business_date_cutoffs(end_exclusive.isoformat(), cfg)
                where.append(timestamp_before_exclusive_sql("measured_at"))
                params.extend((end_utc, end_legacy))
        except (TypeError, ValueError):
            period_valid = False
        if not period_valid:
            where.append("0 = 1")
        r = conn.execute(
            "WITH ranked AS (SELECT value,ROW_NUMBER() OVER ("
            "PARTITION BY tenant_id,content_id,platform,metric_key "
            "ORDER BY measured_at DESC,recorded_at DESC,fact_id DESC) rn "
            "FROM metric_facts_effective WHERE " + " AND ".join(where) + ") "
            "SELECT AVG(value),COUNT(value) FROM ranked WHERE rn=1",
            params,
        ).fetchone()
        per_content, n = (float(r[0]) if r and r[0] is not None else None), int(r[1] or 0)
        rate = GoalProgressUpdater.achievement_rate(
            g.get("current_value"), g.get("baseline_value"), g.get("target_value"), g.get("metric_scope"))
        done_ratio = max(0.0, (rate or 0) / 100.0)
        remaining = max(0.0, float(g["target_value"]) * (1 - done_ratio))
        if per_content and per_content > 0 and n >= policy.minimum_sample_count:
            contents_needed = math.ceil(remaining / per_content)
            notes.append(f"历史每篇平均 {per_content:.1f} {metric}（样本 {n}），剩余 {remaining:.0f} → 约需 {contents_needed} 篇")
        else:
            notes.append(f"已发布样本不足 {policy.minimum_sample_count} 篇，无法按产出推算篇数，先按默认节奏起步，样本达标后再用真实数据重算")
    elif metric in PERCENT_METRICS:
        notes.append(f"比率类目标靠质量而非数量，按每 {policy.default_interval_days} 天一轮、保持每轮复盘调整")
    else:
        notes.append(f"指标 {metric} 暂不支持按产出推算，按每 {policy.default_interval_days} 天一轮起步")

    per_round = topic_count * max(1, len(platforms))
    if contents_needed:
        rounds = max(1, math.ceil(contents_needed / per_round))
        if rounds > period_days:  # 每天一轮也来不及 → 提高每轮篇数
            topic_count = min(policy.max_topics_per_platform, math.ceil(contents_needed / (period_days * max(1, len(platforms)))))
            rounds = period_days
            notes.append(f"按天开轮仍不够，建议每平台每轮提到 {topic_count} 篇")
    else:
        rounds = max(1, period_days // policy.default_interval_days)
    rounds = min(rounds, policy.max_rounds)
    interval = max(1, period_days // rounds)
    notes.append(f"{period_days} 天内开 {rounds} 轮，每 {interval} 天一轮，每轮每平台 {topic_count} 篇")
    return {
        "goal_id": goal_id,
        "suggestion": {
            "topic_count_per_platform": topic_count,
            "enabled_platforms": sorted(platforms),
            "cadence": {"round_interval_days": interval, "rounds_planned": rounds, "goal_id": goal_id},
        },
        "notes": notes,
    }
