"""Immutable SQLite persistence for master plans, platform subplans and stages."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .plan_version import PlanVersionManager


class PlanVersionError(ValueError):
    """Raised when a plan-version transition violates the data contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlanVersionStore:
    """Transactional version repository for one tenant's independent SQLite DB."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._signer = PlanVersionManager(None, conn)

    def _begin(self) -> None:
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")

    # ------------------------------------------------------------------
    # Master plan lifecycle
    # ------------------------------------------------------------------
    def create_master_draft(
        self,
        snapshot: dict[str, Any],
        *,
        author: str = "user",
        source: str = "user",
        based_on_version_id: Optional[int] = None,
        change_note: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            raise PlanVersionError("主计划快照必须是对象")
        if source not in {"user", "review_adoption", "legacy_recovery", "system"}:
            raise PlanVersionError("无效的主计划版本来源")
        signature = self._signer.compute_signature(snapshot)
        now = _now()
        self._begin()
        try:
            if based_on_version_id is not None and self.conn.execute(
                "SELECT 1 FROM master_plan_versions WHERE id=?", (based_on_version_id,)
            ).fetchone() is None:
                raise PlanVersionError("基准主计划版本不存在")
            version_no = int(self.conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM master_plan_versions"
            ).fetchone()[0])
            cur = self.conn.execute(
                """INSERT INTO master_plan_versions(
                    version_no,plan_signature,plan_snapshot,status,source,
                    based_on_version_id,created_at,created_by,change_note
                ) VALUES(?,?,?,'draft',?,?,?,?,?)""",
                (version_no, signature, json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                 source, based_on_version_id, now, author, change_note),
            )
            row = dict(self.conn.execute(
                "SELECT * FROM master_plan_versions WHERE id=?", (cur.lastrowid,)
            ).fetchone())
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise

    def create_master_draft_from_version(
        self, version_id: int, *, author: str = "user", change_note: str = "从历史版本恢复"
    ) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT plan_snapshot FROM master_plan_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row is None:
            raise PlanVersionError("待恢复的历史版本不存在")
        try:
            snapshot = json.loads(row["plan_snapshot"])
        except (TypeError, ValueError) as exc:
            raise PlanVersionError("历史版本快照损坏，拒绝恢复") from exc
        return self.create_master_draft(
            snapshot, author=author, source="user",
            based_on_version_id=version_id, change_note=change_note,
        )

    def activate_master_version(self, version_id: int, *, reviewer: str) -> dict[str, Any]:
        now = _now()
        self._begin()
        try:
            row = self.conn.execute(
                "SELECT * FROM master_plan_versions WHERE id=?", (version_id,)
            ).fetchone()
            if row is None:
                raise PlanVersionError("主计划版本不存在")
            if row["status"] != "draft":
                raise PlanVersionError("只有草稿版本可以审核生效")
            if row["deleted_at"] is not None:
                raise PlanVersionError("已删除的主计划不能审核生效")
            latest = self.conn.execute(
                """SELECT id FROM master_plan_versions
                   WHERE deleted_at IS NULL
                     AND (status IN ('draft','effective') OR effective_at IS NOT NULL)
                   ORDER BY version_no DESC LIMIT 1"""
            ).fetchone()
            if latest is None or int(latest["id"]) != int(version_id):
                raise PlanVersionError("只有最新的未删除主计划可以审核生效")
            snapshot = json.loads(row["plan_snapshot"])
            signature = self._signer.compute_signature(snapshot)
            if signature != row["plan_signature"]:
                raise PlanVersionError("主计划快照与签名不一致，拒绝生效")
            previous_effective = self.conn.execute(
                "SELECT id FROM master_plan_versions WHERE status='effective'"
            ).fetchone()
            if previous_effective is not None:
                # A new mother plan pauses child plans still based on the superseded
                # version. Existing rounds and child snapshots remain untouched.
                self.conn.execute(
                    """UPDATE sub_plans
                       SET status='paused',pause_reason='master_plan_switch',
                           paused_by_master_version_id=?,updated_at=?
                       WHERE master_plan_version_id=? AND status='active'""",
                    (version_id, now, previous_effective["id"]),
                )
            self.conn.execute(
                """UPDATE master_plan_versions
                   SET status='archived',superseded_at=? WHERE status='effective'""", (now,)
            )
            self.conn.execute(
                """UPDATE master_plan_versions
                   SET status='effective',reviewed_at=?,reviewed_by=?,effective_at=?
                   WHERE id=? AND status='draft'""",
                (now, reviewer, now, version_id),
            )
            self.conn.execute(
                """INSERT INTO plan_settings(
                       id,plan_snapshot,plan_signature,updated_at,updated_by,active_version_id
                   ) VALUES(1,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       plan_snapshot=excluded.plan_snapshot,
                       plan_signature=excluded.plan_signature,
                       updated_at=excluded.updated_at,
                       updated_by=excluded.updated_by,
                       active_version_id=excluded.active_version_id""",
                (row["plan_snapshot"], signature, now, reviewer, version_id),
            )
            self.conn.execute(
                """INSERT INTO signature_history(
                       plan_signature,plan_snapshot,round_id,created_at,author
                   ) VALUES(?,?,NULL,?,?)""",
                (signature, row["plan_snapshot"], now, reviewer),
            )
            self.conn.commit()
            return dict(self.conn.execute(
                "SELECT * FROM master_plan_versions WHERE id=?", (version_id,)
            ).fetchone())
        except Exception:
            self.conn.rollback()
            raise

    def delete_master_version(self, version_id: int, *, actor: str = "user") -> dict[str, Any]:
        """Soft-delete the latest effective master and atomically restore its predecessor."""
        now = _now()
        self._begin()
        try:
            current = self.conn.execute(
                "SELECT * FROM master_plan_versions WHERE id=?", (version_id,)
            ).fetchone()
            if current is None:
                raise PlanVersionError("主计划版本不存在")
            if current["deleted_at"] is not None:
                raise PlanVersionError("主计划已经删除")
            if current["status"] != "effective":
                raise PlanVersionError("只能删除当前生效的最新主计划")
            latest = self.conn.execute(
                """SELECT id FROM master_plan_versions
                   WHERE deleted_at IS NULL
                     AND (status IN ('draft','effective') OR effective_at IS NOT NULL)
                   ORDER BY version_no DESC LIMIT 1"""
            ).fetchone()
            if latest is None or int(latest["id"]) != int(version_id):
                raise PlanVersionError("只能删除最新的未删除主计划")
            predecessor = self.conn.execute(
                """SELECT * FROM master_plan_versions
                   WHERE deleted_at IS NULL AND id<>? AND version_no<?
                     AND status='archived' AND effective_at IS NOT NULL
                   ORDER BY version_no DESC LIMIT 1""",
                (version_id, current["version_no"]),
            ).fetchone()
            if predecessor is None:
                raise PlanVersionError("没有可恢复的上一份未删除主计划，拒绝删除以避免系统无生效母计划")

            # The deleted master's currently active children must stop producing.
            self.conn.execute(
                """UPDATE sub_plans
                   SET status='paused',pause_reason='master_plan_deleted',
                       paused_by_master_version_id=?,updated_at=?
                   WHERE master_plan_version_id=? AND status='active'""",
                (version_id, now, version_id),
            )
            self.conn.execute(
                """UPDATE master_plan_versions
                   SET status='archived',deleted_at=?,deleted_by=?,superseded_at=COALESCE(superseded_at,?)
                   WHERE id=? AND status='effective' AND deleted_at IS NULL""",
                (now, actor, now, version_id),
            )
            if self.conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise PlanVersionError("主计划状态已变化，请刷新后重试")

            self.conn.execute(
                """UPDATE master_plan_versions
                   SET status='effective',reviewed_at=COALESCE(reviewed_at,?),
                       reviewed_by=COALESCE(reviewed_by,?),effective_at=COALESCE(effective_at,?)
                   WHERE id=? AND status='archived' AND deleted_at IS NULL""",
                (now, actor, now, predecessor["id"]),
            )
            if self.conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise PlanVersionError("上一份主计划无法恢复")
            # Restore only children paused by this exact master-plan switch.
            self.conn.execute(
                """UPDATE sub_plans
                   SET status='active',pause_reason=NULL,paused_by_master_version_id=NULL,updated_at=?
                   WHERE master_plan_version_id=? AND status='paused'
                     AND pause_reason='master_plan_switch'
                     AND paused_by_master_version_id=?""",
                (now, predecessor["id"], version_id),
            )
            self.conn.execute(
                """UPDATE plan_settings
                   SET plan_snapshot=?,plan_signature=?,updated_at=?,updated_by=?,active_version_id=?
                   WHERE id=1""",
                (predecessor["plan_snapshot"], predecessor["plan_signature"],
                 now, actor, predecessor["id"]),
            )
            if self.conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise PlanVersionError("母计划设置记录缺失，无法安全恢复")
            self.conn.execute(
                """INSERT INTO signature_history(
                       plan_signature,plan_snapshot,round_id,created_at,author
                   ) VALUES(?,?,NULL,?,?)""",
                (predecessor["plan_signature"], predecessor["plan_snapshot"], now, actor),
            )
            self.conn.commit()
            return {
                "deleted_version": dict(self.conn.execute(
                    "SELECT * FROM master_plan_versions WHERE id=?", (version_id,)
                ).fetchone()),
                "restored_version": dict(self.conn.execute(
                    "SELECT * FROM master_plan_versions WHERE id=?", (predecessor["id"],)
                ).fetchone()),
            }
        except Exception:
            self.conn.rollback()
            raise

    def list_master_versions(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM master_plan_versions ORDER BY version_no DESC LIMIT ?", (limit,)
        ).fetchall()]

    # ------------------------------------------------------------------
    # Platform subplan identity and immutable versions
    # ------------------------------------------------------------------
    def create_subplan(
        self,
        sub_plan_id: str,
        *,
        master_plan_version_id: int,
        platform: str,
        title: str,
        author: str = "user",
    ) -> dict[str, Any]:
        allowed_platforms = {"xhs", "douyin", "kuaishou", "wechat_channels"}
        title = str(title or "").strip()
        if platform not in allowed_platforms:
            raise PlanVersionError("子计划平台无效")
        if not title or len(title) > 160:
            raise PlanVersionError("子计划标题必须为 1-160 个字符")
        now = _now()
        self._begin()
        try:
            master = self.conn.execute(
                "SELECT status FROM master_plan_versions WHERE id=?", (master_plan_version_id,)
            ).fetchone()
            if master is None:
                raise PlanVersionError("关联的主计划版本不存在")
            if master["status"] != "effective":
                raise PlanVersionError("新子计划必须关联当前生效的主计划版本")
            self.conn.execute(
                """INSERT INTO sub_plans(
                    id,master_plan_version_id,platform,title,status,created_at,updated_at,created_by
                ) VALUES(?,?,?,?,'draft',?,?,?)""",
                (sub_plan_id, master_plan_version_id, platform, title, now, now, author),
            )
            row = dict(self.conn.execute(
                "SELECT * FROM sub_plans WHERE id=?", (sub_plan_id,)
            ).fetchone())
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise

    def create_subplan_version(
        self,
        sub_plan_id: str,
        snapshot: dict[str, Any],
        *,
        master_plan_version_id: int,
        author: str = "user",
        stages: Optional[list[dict[str, Any]]] = None,
        based_on_version_id: Optional[int] = None,
        change_note: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            raise PlanVersionError("子计划快照必须是对象")
        if snapshot.get("title") is not None:
            title = str(snapshot["title"]).strip()
            if not title or len(title) > 160:
                raise PlanVersionError("子计划标题必须为 1-160 个字符")
        stages = stages or []
        if not isinstance(stages, list):
            raise PlanVersionError("阶段必须是数组")
        now = _now()
        self._begin()
        try:
            identity = self.conn.execute(
                "SELECT id FROM sub_plans WHERE id=?", (sub_plan_id,)
            ).fetchone()
            if identity is None:
                raise PlanVersionError("子计划不存在")
            master = self.conn.execute(
                "SELECT status FROM master_plan_versions WHERE id=?", (master_plan_version_id,)
            ).fetchone()
            if master is None:
                raise PlanVersionError("关联的主计划版本不存在")
            if master["status"] != "effective":
                raise PlanVersionError("新子计划版本必须关联当前生效的主计划版本")
            base_stage_keys: set[str] = set()
            if based_on_version_id is not None:
                base = self.conn.execute(
                    "SELECT sub_plan_id FROM sub_plan_versions WHERE id=?", (based_on_version_id,)
                ).fetchone()
                if base is None or base["sub_plan_id"] != sub_plan_id:
                    raise PlanVersionError("基准版本不属于当前子计划")
                base_stage_keys = {
                    str(row["stage_key"])
                    for row in self.conn.execute(
                        "SELECT stage_key FROM sub_plan_stages WHERE sub_plan_version_id=?",
                        (based_on_version_id,),
                    ).fetchall()
                }

            # Assign stable keys before validating dependency edges. Existing stages
            # carry their keys forward; newly added stages receive new stable keys.
            normalized_stages = []
            for index, raw_stage in enumerate(stages, start=1):
                if not isinstance(raw_stage, dict):
                    raise PlanVersionError("阶段条目必须是对象")
                stage = dict(raw_stage)
                stage_key = str(stage.get("stage_key") or uuid.uuid4().hex).strip()
                if not stage_key or len(stage_key) > 100:
                    raise PlanVersionError("阶段稳定标识无效")
                if based_on_version_id is not None and stage_key not in base_stage_keys:
                    foreign_owner = self.conn.execute(
                        """SELECT 1 FROM sub_plan_stages s
                           JOIN sub_plan_versions v ON v.id=s.sub_plan_version_id
                           WHERE s.stage_key=? AND v.sub_plan_id<>? LIMIT 1""",
                        (stage_key, sub_plan_id),
                    ).fetchone()
                    if foreign_owner is not None:
                        raise PlanVersionError("阶段稳定标识必须继承自当前子计划，不能引用其他子计划的阶段")
                # Existing stages retain their keys; newly added stages may introduce a fresh key.
                stage["stage_key"] = stage_key
                deps = stage.get("dependencies", stage.get("depends_on", []))
                if not isinstance(deps, list) or any(not isinstance(dep, str) or not dep.strip() for dep in deps):
                    raise PlanVersionError("阶段依赖必须是稳定阶段标识数组")
                deps = [dep.strip() for dep in deps]
                if len(set(deps)) != len(deps) or stage_key in deps:
                    raise PlanVersionError("阶段依赖不能重复或依赖自身")
                stage["dependencies"] = deps
                normalized_stages.append(stage)
            all_keys = {stage["stage_key"] for stage in normalized_stages}
            if len(all_keys) != len(normalized_stages):
                raise PlanVersionError("阶段稳定标识无效或重复")
            graph = {stage["stage_key"]: stage["dependencies"] for stage in normalized_stages}
            for key, deps in graph.items():
                if any(dep not in all_keys for dep in deps):
                    raise PlanVersionError(f"阶段 {key} 引用了不存在的前置阶段")
            visiting, visited = set(), set()
            def visit(key):
                if key in visiting:
                    raise PlanVersionError("阶段依赖关系存在循环")
                if key in visited:
                    return
                visiting.add(key)
                for dep in graph[key]:
                    visit(dep)
                visiting.remove(key)
                visited.add(key)
            for key in graph:
                visit(key)

            version_no = int(self.conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM sub_plan_versions WHERE sub_plan_id=?",
                (sub_plan_id,),
            ).fetchone()[0])
            cur = self.conn.execute(
                """INSERT INTO sub_plan_versions(
                    sub_plan_id,version_no,master_plan_version_id,snapshot_json,status,
                    based_on_version_id,created_at,created_by,change_note
                ) VALUES(?,?,?,?,'draft',?,?,?,?)""",
                (sub_plan_id, version_no, master_plan_version_id,
                 json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                 based_on_version_id, now, author, change_note),
            )
            version_id = int(cur.lastrowid)
            seen_stage_keys: set[str] = set()
            for index, stage in enumerate(normalized_stages, start=1):
                stage_key = stage["stage_key"]
                if stage_key in seen_stage_keys:
                    raise PlanVersionError("阶段稳定标识无效或重复")
                seen_stage_keys.add(stage_key)
                title = str(stage.get("title") or "").strip()
                if not title or len(title) > 160:
                    raise PlanVersionError("阶段标题必须为 1-160 个字符")
                raw_order = stage.get("stage_order", index)
                raw_target = stage.get("target_count", 1)
                if raw_order is None or raw_target is None or isinstance(raw_order, bool) or isinstance(raw_target, bool):
                    raise PlanVersionError("阶段顺序和目标篇数必须是整数")
                try:
                    stage_order = int(raw_order)
                    target_count = int(raw_target)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise PlanVersionError("阶段顺序和目标篇数必须是整数") from exc
                if str(raw_order).strip() != str(stage_order) or str(raw_target).strip() != str(target_count):
                    raise PlanVersionError("阶段顺序和目标篇数必须是整数")
                if stage_order < 1:
                    raise PlanVersionError("阶段顺序必须大于等于 1")
                if not 1 <= target_count <= 10000:
                    raise PlanVersionError("阶段目标篇数必须在 1 到 10000 之间")
                start_date = stage.get("start_date")
                end_date = stage.get("end_date")
                for field_name, date_value in (("start_date", start_date), ("end_date", end_date)):
                    if date_value is not None:
                        try:
                            datetime.strptime(str(date_value), "%Y-%m-%d")
                        except ValueError as exc:
                            raise PlanVersionError(f"{field_name} 必须为 YYYY-MM-DD") from exc
                stage_status = str(stage.get("status") or "pending")
                if stage_status not in {"pending", "in_progress", "completed", "paused", "cancelled"}:
                    raise PlanVersionError("阶段状态无效")
                entry_met = stage.get("entry_condition_met", True)
                if isinstance(entry_met, int) and not isinstance(entry_met, bool) and entry_met in (0, 1):
                    entry_met = bool(entry_met)
                if not isinstance(entry_met, bool):
                    raise PlanVersionError("阶段进入条件状态必须是布尔值")
                completion_criteria = str(stage.get("completion_criteria") or stage.get("completion_condition") or "")[:4000]
                business_required = stage.get("business_completion_required", bool(completion_criteria.strip()))
                effect_required = stage.get("effect_required", bool(str(stage.get("effect_evaluation") or "").strip()))
                for field_name, value in (("business_completion_required", business_required),
                                          ("effect_required", effect_required)):
                    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
                        if field_name == "business_completion_required":
                            business_required = bool(value)
                        else:
                            effect_required = bool(value)
                    elif not isinstance(value, bool):
                        raise PlanVersionError(f"{field_name} 必须是布尔值")
                business_status = "pending" if business_required else "not_required"
                effect_status = "pending" if effect_required else "not_required"
                self.conn.execute(
                    """INSERT INTO sub_plan_stages(
                        sub_plan_version_id,stage_key,stage_order,title,direction,target_count,
                        start_date,end_date,completion_condition,status,notes,created_at,updated_at,
                        strategy_goal,current_problem,execution_strategy,prerequisites,input_info,
                        expected_result,completion_criteria,effect_evaluation,entry_condition,
                        entry_condition_met,dependencies_json,business_completion_required,
                        business_completion_status,effect_required,effect_status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (version_id, stage_key, stage_order, title,
                     str(stage.get("direction") or ""), target_count,
                     start_date, end_date, str(stage.get("completion_condition") or ""),
                     stage_status, str(stage.get("notes") or ""), now, now,
                     str(stage.get("strategy_goal") or "")[:4000],
                     str(stage.get("current_problem") or "")[:4000],
                     str(stage.get("execution_strategy") or "")[:8000],
                     str(stage.get("prerequisites") or "")[:4000],
                     str(stage.get("input_info") or "")[:4000],
                     str(stage.get("expected_result") or "")[:4000],
                     completion_criteria,
                     str(stage.get("effect_evaluation") or "")[:4000],
                     str(stage.get("entry_condition") or "")[:4000],
                     1 if entry_met else 0,
                     json.dumps(stage.get("dependencies") or [], ensure_ascii=False),
                     1 if business_required else 0, business_status,
                     1 if effect_required else 0, effect_status),
                )
            row = dict(self.conn.execute(
                "SELECT * FROM sub_plan_versions WHERE id=?", (version_id,)
            ).fetchone())
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise

    def activate_subplan_version(self, version_id: int, *, reviewer: str) -> dict[str, Any]:
        now = _now()
        self._begin()
        try:
            row = self.conn.execute(
                "SELECT * FROM sub_plan_versions WHERE id=?", (version_id,)
            ).fetchone()
            if row is None:
                raise PlanVersionError("子计划版本不存在")
            if row["status"] != "draft":
                raise PlanVersionError("只有子计划草稿版本可以审核生效")
            identity = self.conn.execute(
                "SELECT * FROM sub_plans WHERE id=?", (row["sub_plan_id"],)
            ).fetchone()
            if identity is None:
                raise PlanVersionError("子计划身份不存在")
            master = self.conn.execute(
                "SELECT status FROM master_plan_versions WHERE id=?",
                (row["master_plan_version_id"],),
            ).fetchone()
            if master is None or master["status"] != "effective":
                raise PlanVersionError("子计划版本只能在其主计划版本生效期间审核")
            self.conn.execute(
                """UPDATE sub_plan_versions SET status='archived',superseded_at=?
                   WHERE sub_plan_id=? AND status='effective'""", (now, row["sub_plan_id"])
            )
            self.conn.execute(
                """UPDATE sub_plan_versions
                   SET status='effective',reviewed_at=?,reviewed_by=?,effective_at=?
                   WHERE id=? AND status='draft'""",
                (now, reviewer, now, version_id),
            )
            approved_snapshot = json.loads(row["snapshot_json"])
            approved_title = str(approved_snapshot.get("title") or identity["title"]).strip()
            self.conn.execute(
                """UPDATE sub_plans SET current_version_id=?,master_plan_version_id=?,
                       title=?,status='active',updated_at=? WHERE id=?""",
                (version_id, row["master_plan_version_id"], approved_title,
                 now, row["sub_plan_id"]),
            )
            self.conn.commit()
            return dict(self.conn.execute(
                "SELECT * FROM sub_plan_versions WHERE id=?", (version_id,)
            ).fetchone())
        except Exception:
            self.conn.rollback()
            raise

    def reject_subplan_version(self, version_id: int, *, reviewer: str, reason: str) -> dict[str, Any]:
        reason = str(reason or "").strip()
        if not reason:
            raise PlanVersionError("审核拒绝必须填写原因")
        cur = self.conn.execute(
            """UPDATE sub_plan_versions
               SET status='rejected',reviewed_at=?,reviewed_by=?,review_note=?
               WHERE id=? AND status='draft'""",
            (_now(), reviewer, reason[:1000], version_id),
        )
        if cur.rowcount != 1:
            raise PlanVersionError("只有存在的草稿版本可以审核拒绝")
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM sub_plan_versions WHERE id=?", (version_id,)
        ).fetchone())

    # ------------------------------------------------------------------
    # Stage lifecycle and publish progress
    # ------------------------------------------------------------------
    def update_stage_status(self, stage_id: int, status: str) -> dict[str, Any]:
        allowed = {"pending", "in_progress", "completed", "paused", "cancelled"}
        if status not in allowed:
            raise PlanVersionError("无效的阶段状态")
        row = self.conn.execute(
            "SELECT status FROM sub_plan_stages WHERE id=?", (stage_id,)
        ).fetchone()
        if row is None:
            raise PlanVersionError("阶段不存在")
        previous = row["status"]
        transitions = {
            "pending": {"pending", "in_progress", "paused", "cancelled"},
            "in_progress": {"in_progress", "completed", "paused", "cancelled"},
            "paused": {"paused", "in_progress", "cancelled"},
            "completed": {"completed"},
            "cancelled": {"cancelled"},
        }
        if status == "completed":
            from .strategy_step_completion import assert_stage_completion_ready
            try:
                assert_stage_completion_ready(self.conn, stage_id)
            except ValueError as exc:
                raise PlanVersionError(str(exc)) from exc
        if status not in transitions[previous]:
            raise PlanVersionError(f"阶段状态不允许从 {previous} 变更为 {status}")
        cur = self.conn.execute(
            "UPDATE sub_plan_stages SET status=?,updated_at=? WHERE id=? AND status=?",
            (status, _now(), stage_id, previous),
        )
        self.conn.commit()
        if cur.rowcount != 1:
            raise PlanVersionError("阶段状态已被并发修改，请刷新后重试")
        return dict(self.conn.execute(
            "SELECT * FROM sub_plan_stages WHERE id=?", (stage_id,)
        ).fetchone())

    def get_stage_publish_stats(self, stage_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM sub_plan_stage_publish_stats WHERE stage_id=?", (stage_id,)
        ).fetchone()
        if row is None:
            raise PlanVersionError("阶段发布统计不存在")
        return dict(row)

    # ------------------------------------------------------------------
    # Immutable round binding and persistent opening idempotency
    # ------------------------------------------------------------------
    def bind_round(
        self,
        round_id: int,
        *,
        master_plan_version_id: int,
        sub_plan_id: Optional[str] = None,
        sub_plan_version_id: Optional[int] = None,
        sub_plan_stage_id: Optional[int] = None,
    ) -> None:
        self._begin()
        try:
            master = self.conn.execute(
                "SELECT plan_signature FROM master_plan_versions WHERE id=?",
                (master_plan_version_id,),
            ).fetchone()
            if master is None:
                raise PlanVersionError("轮次绑定的主计划版本不存在")
            existing_round = self.conn.execute(
                """SELECT master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id
                   FROM spiral_rounds WHERE id=?""", (round_id,)
            ).fetchone()
            if existing_round is None:
                raise PlanVersionError("轮次不存在")

            sub_snapshot = stage_snapshot = None
            if any(value is not None for value in (sub_plan_id, sub_plan_version_id, sub_plan_stage_id)):
                if not all(value is not None for value in (sub_plan_id, sub_plan_version_id, sub_plan_stage_id)):
                    raise PlanVersionError("子计划 ID、版本 ID 和阶段 ID 必须同时提供")
                version = self.conn.execute(
                    """SELECT v.* FROM sub_plan_versions v
                       JOIN sub_plans p ON p.id=v.sub_plan_id
                       JOIN master_plan_versions m ON m.id=v.master_plan_version_id
                       WHERE v.id=? AND v.sub_plan_id=? AND v.master_plan_version_id=?
                         AND v.status='effective' AND p.status='active'
                         AND p.current_version_id=v.id
                         AND p.master_plan_version_id=? AND m.status='effective'""",
                    (sub_plan_version_id, sub_plan_id, master_plan_version_id, master_plan_version_id),
                ).fetchone()
                if version is None:
                    raise PlanVersionError("子计划版本必须是所属生效主计划下的有效版本")
                stage = self.conn.execute(
                    """SELECT * FROM sub_plan_stages
                       WHERE id=? AND sub_plan_version_id=?""",
                    (sub_plan_stage_id, sub_plan_version_id),
                ).fetchone()
                if stage is None:
                    raise PlanVersionError("阶段不属于指定的子计划版本")
                ready_stage = self.assert_stage_ready(
                    int(sub_plan_stage_id), int(sub_plan_version_id)
                )
                sub_snapshot = version["snapshot_json"]
                stage_snapshot = json.dumps(ready_stage, ensure_ascii=False, sort_keys=True)

            desired_binding = (master_plan_version_id, sub_plan_id, sub_plan_version_id, sub_plan_stage_id)
            existing_binding = tuple(existing_round)
            if any(value is not None for value in existing_binding) and existing_binding != desired_binding:
                raise PlanVersionError("轮次版本归属不可覆盖；历史轮次必须保留原始绑定")
            cur = self.conn.execute(
                """UPDATE spiral_rounds SET master_plan_version_id=?,sub_plan_id=?,
                       sub_plan_version_id=?,sub_plan_stage_id=?,sub_plan_snapshot=?,
                       sub_plan_stage_snapshot=?,plan_signature=?,plan_attribution_status='resolved'
                   WHERE id=?""",
                (master_plan_version_id, sub_plan_id, sub_plan_version_id,
                 sub_plan_stage_id, sub_snapshot, stage_snapshot,
                 master["plan_signature"], round_id),
            )
            if cur.rowcount != 1:
                raise PlanVersionError("轮次不存在")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def assert_stage_ready(self, stage_id: int, version_id: int) -> dict[str, Any]:
        """Reject execution until all declared predecessors have completed and reported outcomes."""
        stage = self.conn.execute(
            """SELECT s.*,v.sub_plan_id FROM sub_plan_stages s
               JOIN sub_plan_versions v ON v.id=s.sub_plan_version_id
               WHERE s.id=? AND s.sub_plan_version_id=?""",
            (stage_id, version_id),
        ).fetchone()
        if stage is None:
            raise PlanVersionError("阶段不属于指定子计划版本")
        if not bool(stage["entry_condition_met"]):
            raise PlanVersionError("阶段进入条件尚未满足")
        from .strategy_step_completion import completion_gate_state
        gate_state = completion_gate_state(dict(stage))
        if gate_state["production_blocked"]:
            raise PlanVersionError("策略完成条件未通过或等待人工复核，必须先调整策略或完成复核")
        try:
            dependencies = json.loads(stage["dependencies_json"] or "[]")
        except (TypeError, ValueError) as exc:
            raise PlanVersionError("阶段依赖快照损坏") from exc
        if not isinstance(dependencies, list):
            raise PlanVersionError("阶段依赖快照格式无效")
        dependency_outcomes = {}
        for dependency_key in dependencies:
            predecessor = self.conn.execute(
                """SELECT s.*,COALESCE(stats.confirmed_published_count,0) AS confirmed_published_count
                   FROM sub_plan_stages s
                   LEFT JOIN sub_plan_stage_publish_stats stats ON stats.stage_id=s.id
                   WHERE s.sub_plan_version_id=? AND s.stage_key=?""",
                (version_id, dependency_key),
            ).fetchone()
            outcome = self.conn.execute(
                """SELECT result_json,evaluation,recorded_at FROM sub_plan_stage_outcomes
                   WHERE sub_plan_id=? AND stage_key=?
                   ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                (stage["sub_plan_id"], dependency_key),
            ).fetchone()
            if predecessor is None or predecessor["status"] != "completed" or outcome is None:
                raise PlanVersionError(
                    f"前置阶段 {dependency_key} 尚未完成或缺少实际效果记录"
                )
            predecessor_gates = completion_gate_state(dict(predecessor))
            if not predecessor_gates["can_complete"]:
                raise PlanVersionError(
                    f"前置阶段 {dependency_key} 的业务完成或效果条件已失效，不能进入后续步骤"
                )
            if not str(outcome["evaluation"] or "").strip():
                raise PlanVersionError(f"前置阶段 {dependency_key} 缺少效果评价，不能进入后续步骤")
            try:
                actual_result = json.loads(outcome["result_json"])
                if not isinstance(actual_result, dict) or not actual_result:
                    raise ValueError("empty or invalid result")
                dependency_outcomes[dependency_key] = {
                    "result": actual_result,
                    "evaluation": str(outcome["evaluation"]).strip(),
                    "recorded_at": outcome["recorded_at"],
                }
            except (TypeError, ValueError) as exc:
                raise PlanVersionError(f"前置阶段 {dependency_key} 的实际结果损坏或为空") from exc
        ready = dict(stage)
        ready["dependency_outcomes"] = dependency_outcomes
        return ready

    def record_stage_outcome(
        self, stage_id: int, version_id: int, result: dict[str, Any], *,
        evaluation: str = "", author: str = "user",
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise PlanVersionError("阶段实际结果必须是对象")
        stage = self.conn.execute(
            """SELECT s.stage_key,s.status,v.sub_plan_id
               FROM sub_plan_stages s JOIN sub_plan_versions v ON v.id=s.sub_plan_version_id
               WHERE s.id=? AND s.sub_plan_version_id=?""",
            (stage_id, version_id),
        ).fetchone()
        if stage is None:
            raise PlanVersionError("阶段不属于指定子计划版本")
        if stage["status"] not in ("in_progress", "completed"):
            raise PlanVersionError("只有执行中或已完成的阶段才能记录实际效果")
        published = self.conn.execute(
            "SELECT COALESCE(confirmed_published_count,0) FROM sub_plan_stage_publish_stats WHERE stage_id=?",
            (stage_id,),
        ).fetchone()
        if int(published[0] or 0) < int(self.conn.execute(
            "SELECT target_count FROM sub_plan_stages WHERE id=?", (stage_id,)
        ).fetchone()[0]):
            raise PlanVersionError("发布数量目标尚未达标，不能提交阶段实际结果")
        self._begin()
        try:
            cur = self.conn.execute(
                """INSERT INTO sub_plan_stage_outcomes(
                    sub_plan_id,stage_key,source_version_id,stage_id,result_json,
                    evaluation,recorded_at,recorded_by
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (stage["sub_plan_id"], stage["stage_key"], version_id, stage_id,
                 json.dumps(result, ensure_ascii=False, sort_keys=True),
                 str(evaluation or "")[:4000], _now(), str(author or "user")[:120]),
            )
            row = dict(self.conn.execute(
                "SELECT * FROM sub_plan_stage_outcomes WHERE id=?", (cur.lastrowid,)
            ).fetchone())
            row["result"] = json.loads(row.pop("result_json"))
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise

    def reserve_round_open(
        self,
        idempotency_key: str,
        request_hash: str,
        *,
        master_plan_version_id: int,
        sub_plan_id: str,
        sub_plan_version_id: int,
        sub_plan_stage_id: int,
        lease_seconds: int = 300,
    ) -> dict[str, Any]:
        """Acquire a tenant-local durable lease for one subplan/version/stage slot."""
        key = str(idempotency_key or "").strip()
        digest = str(request_hash or "").strip()
        if not key or len(key) > 200 or not digest or len(digest) > 128:
            raise PlanVersionError("自动开轮幂等键或请求摘要无效")
        version = self.conn.execute(
            """SELECT 1 FROM sub_plan_versions v
               JOIN sub_plans p ON p.id=v.sub_plan_id
               JOIN master_plan_versions m ON m.id=v.master_plan_version_id
               WHERE v.id=? AND v.sub_plan_id=? AND v.master_plan_version_id=?
                 AND v.status='effective' AND p.status='active'
                 AND p.current_version_id=v.id
                 AND p.master_plan_version_id=? AND m.status='effective'""",
            (sub_plan_version_id, sub_plan_id, master_plan_version_id, master_plan_version_id),
        ).fetchone()
        stage = self.conn.execute(
            """SELECT id FROM sub_plan_stages
               WHERE id=? AND sub_plan_version_id=? AND status IN ('pending','in_progress')""",
            (sub_plan_stage_id, sub_plan_version_id),
        ).fetchone()
        if version is None or stage is None:
            raise PlanVersionError("自动开轮必须绑定有效子计划版本及可执行阶段")
        self.assert_stage_ready(sub_plan_stage_id, sub_plan_version_id)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="seconds")
        lease_until = (now_dt + timedelta(seconds=max(30, min(int(lease_seconds), 3600)))).isoformat(timespec="seconds")
        self._begin()
        try:
            row = self.conn.execute(
                "SELECT * FROM round_open_idempotency WHERE idempotency_key=?", (key,)
            ).fetchone()
            expected = (digest, int(master_plan_version_id), sub_plan_id,
                        int(sub_plan_version_id), int(sub_plan_stage_id))
            if row is not None:
                actual = (row["request_hash"], int(row["master_plan_version_id"]),
                          row["sub_plan_id"], int(row["sub_plan_version_id"]),
                          int(row["sub_plan_stage_id"]))
                if actual != expected:
                    raise PlanVersionError("相同幂等键对应不同开轮请求，拒绝重复执行")
                if row["status"] == "created":
                    self.conn.commit()
                    return {"acquired": False, "status": "created", "round_id": row["round_id"]}
                if row["status"] == "reserved" and row["lease_until"] and row["lease_until"] > now:
                    self.conn.commit()
                    return {"acquired": False, "status": "in_progress", "round_id": None}
                self.conn.execute(
                    """UPDATE round_open_idempotency
                       SET status='reserved',lease_until=?,updated_at=?,error_message=NULL
                       WHERE idempotency_key=?""", (lease_until, now, key)
                )
            else:
                self.conn.execute(
                    """INSERT INTO round_open_idempotency(
                        idempotency_key,request_hash,master_plan_version_id,sub_plan_id,
                        sub_plan_version_id,sub_plan_stage_id,status,lease_until,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,'reserved',?,?,?)""",
                    (key, digest, master_plan_version_id, sub_plan_id,
                     sub_plan_version_id, sub_plan_stage_id, lease_until, now, now),
                )
            self.conn.commit()
            return {"acquired": True, "status": "reserved", "round_id": None, "lease_until": lease_until}
        except Exception:
            self.conn.rollback()
            raise

    def create_round_once(
        self,
        idempotency_key: str,
        *,
        goal_id: Optional[str] = None,
        round_number: Optional[int] = None,
        topic_ids: Optional[list[str]] = None,
        content_variants: Optional[list[dict[str, Any]]] = None,
        trigger_source: str = "auto",
        applied_feedback: Optional[str] = None,
    ) -> dict[str, Any]:
        """Atomically create, bind and complete one leased round.

        A crash before commit leaves no round; a crash after commit can safely retry
        and receives the already-created round_id from the durable idempotency row.
        """
        now = _now()
        self._begin()
        try:
            lock = self.conn.execute(
                "SELECT * FROM round_open_idempotency WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if lock is None:
                raise PlanVersionError("自动开轮租约不存在")
            if lock["status"] == "created":
                self.conn.commit()
                return {"status": "created", "round_id": lock["round_id"], "deduped": True}
            if lock["status"] != "reserved" or not lock["lease_until"] or lock["lease_until"] <= now:
                raise PlanVersionError("自动开轮租约已失效")
            version = self.conn.execute(
                """SELECT v.snapshot_json,v.master_plan_version_id,v.sub_plan_id,
                          p.status AS subplan_status,m.plan_signature,m.status AS master_status
                   FROM sub_plan_versions v
                   JOIN sub_plans p ON p.id=v.sub_plan_id
                   JOIN master_plan_versions m ON m.id=v.master_plan_version_id
                   WHERE v.id=? AND v.sub_plan_id=? AND v.status='effective'
                     AND p.current_version_id=v.id
                     AND p.master_plan_version_id=?""",
                (lock["sub_plan_version_id"], lock["sub_plan_id"],
                 lock["master_plan_version_id"]),
            ).fetchone()
            stage = self.conn.execute(
                """SELECT * FROM sub_plan_stages
                   WHERE id=? AND sub_plan_version_id=?""",
                (lock["sub_plan_stage_id"], lock["sub_plan_version_id"]),
            ).fetchone()
            if (version is None or stage is None
                    or version["master_plan_version_id"] != lock["master_plan_version_id"]
                    or version["master_status"] != "effective"
                    or version["subplan_status"] != "active"
                    or stage["status"] not in ("pending", "in_progress")):
                raise PlanVersionError("自动开轮的主计划/子计划/阶段授权已失效")
            if round_number is None:
                round_number = int(self.conn.execute(
                    "SELECT COALESCE(MAX(round_number),0)+1 FROM spiral_rounds"
                ).fetchone()[0])
            ready_stage = self.assert_stage_ready(
                int(lock["sub_plan_stage_id"]), int(lock["sub_plan_version_id"])
            )
            stage_snapshot = json.dumps(ready_stage, ensure_ascii=False, sort_keys=True)
            topics_json = json.dumps(topic_ids or [], ensure_ascii=False)
            variants_json = json.dumps(content_variants or [], ensure_ascii=False)
            cur = self.conn.execute(
                """INSERT INTO spiral_rounds(
                    goal_id,round_number,status,topic_ids,content_variants,trigger_source,applied_feedback,
                    master_plan_version_id,sub_plan_id,sub_plan_version_id,sub_plan_stage_id,
                    sub_plan_snapshot,sub_plan_stage_snapshot,plan_signature,plan_attribution_status
                ) VALUES(?,?,'created',?,?,?,?,?,?,?,?,?,?,?,'resolved')""",
                (goal_id, round_number, topics_json, variants_json, trigger_source, applied_feedback,
                 lock["master_plan_version_id"], lock["sub_plan_id"],
                 lock["sub_plan_version_id"], lock["sub_plan_stage_id"],
                 version["snapshot_json"], stage_snapshot, version["plan_signature"]),
            )
            round_id = int(cur.lastrowid)
            self.conn.execute(
                "UPDATE sub_plans SET updated_at=? WHERE id=?",
                (now, lock["sub_plan_id"]),
            )
            self.conn.execute(
                """UPDATE round_open_idempotency
                   SET status='created',round_id=?,lease_until=NULL,updated_at=?
                   WHERE idempotency_key=? AND status='reserved'""",
                (round_id, now, idempotency_key),
            )
            self.conn.commit()
            return {"status": "created", "round_id": round_id, "deduped": False}
        except Exception:
            self.conn.rollback()
            raise

    def fail_round_open(self, idempotency_key: str, error_message: str) -> None:
        self.conn.execute(
            """UPDATE round_open_idempotency
               SET status='failed',lease_until=NULL,updated_at=?,error_message=?
               WHERE idempotency_key=? AND status='reserved'""",
            (_now(), str(error_message or "开轮失败")[:500], idempotency_key),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Read-only consistency audit and legacy attribution queue
    # ------------------------------------------------------------------
    def validate_integrity(self) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        fk_errors = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            issues.append({"code": "foreign_key_violation", "count": len(fk_errors)})

        versions = self.conn.execute(
            "SELECT id,plan_signature,plan_snapshot FROM master_plan_versions"
        ).fetchall()
        invalid_signatures = []
        for row in versions:
            try:
                actual = self._signer.compute_signature(json.loads(row["plan_snapshot"]))
            except (TypeError, ValueError):
                actual = None
            if actual != row["plan_signature"]:
                invalid_signatures.append(int(row["id"]))
        if invalid_signatures:
            issues.append({"code": "master_plan_signature_mismatch", "version_ids": invalid_signatures})

        mismatched_rounds = self.conn.execute(
            """SELECT r.id FROM spiral_rounds r
               LEFT JOIN master_plan_versions v ON v.id=r.master_plan_version_id
               WHERE r.master_plan_version_id IS NOT NULL
                 AND (v.id IS NULL OR r.plan_signature IS NULL
                      OR r.plan_signature != v.plan_signature)"""
        ).fetchall()
        if mismatched_rounds:
            issues.append({"code": "round_master_version_mismatch",
                           "round_ids": [int(r["id"]) for r in mismatched_rounds]})

        bad_subplan_links = self.conn.execute(
            """SELECT r.id FROM spiral_rounds r
               LEFT JOIN sub_plan_versions v
                 ON v.id=r.sub_plan_version_id AND v.sub_plan_id=r.sub_plan_id
                AND v.master_plan_version_id=r.master_plan_version_id
               LEFT JOIN sub_plan_stages s
                 ON s.id=r.sub_plan_stage_id AND s.sub_plan_version_id=r.sub_plan_version_id
               WHERE (r.sub_plan_id IS NULL AND
                      (r.sub_plan_version_id IS NOT NULL OR r.sub_plan_stage_id IS NOT NULL))
                  OR (r.sub_plan_id IS NOT NULL AND
                      (v.id IS NULL OR s.id IS NULL OR r.sub_plan_snapshot IS NULL
                       OR r.sub_plan_stage_snapshot IS NULL OR r.sub_plan_snapshot!=v.snapshot_json))"""
        ).fetchall()
        if bad_subplan_links:
            issues.append({"code": "round_subplan_stage_mismatch",
                           "round_ids": [int(r["id"]) for r in bad_subplan_links]})

        bad_subplan_pointers = self.conn.execute(
            """SELECT p.id FROM sub_plans p
               LEFT JOIN sub_plan_versions v ON v.id=p.current_version_id
               LEFT JOIN master_plan_versions m ON m.id=p.master_plan_version_id
               WHERE p.status='active'
                 AND (v.id IS NULL OR v.sub_plan_id!=p.id OR v.status!='effective'
                      OR v.master_plan_version_id!=p.master_plan_version_id
                      OR m.id IS NULL OR m.status!='effective')"""
        ).fetchall()
        if bad_subplan_pointers:
            issues.append({
                "code": "subplan_current_version_mismatch",
                "sub_plan_ids": [r["id"] for r in bad_subplan_pointers],
            })

        pending = self.conn.execute(
            """SELECT id FROM spiral_rounds
               WHERE master_plan_version_id IS NULL OR plan_attribution_status!='resolved'"""
        ).fetchall()
        effective_count = int(self.conn.execute(
            "SELECT COUNT(*) FROM master_plan_versions WHERE status='effective'"
        ).fetchone()[0])
        if effective_count > 1:
            issues.append({"code": "multiple_effective_master_versions", "count": effective_count})

        pointer = self.conn.execute(
            "SELECT active_version_id FROM plan_settings WHERE id=1"
        ).fetchone()
        effective = self.conn.execute(
            "SELECT id FROM master_plan_versions WHERE status='effective'"
        ).fetchone()
        if pointer is not None and (
            effective is None or pointer["active_version_id"] != effective["id"]
        ):
            issues.append({"code": "active_plan_pointer_mismatch"})

        return {
            "ok": not issues,
            "issues": issues,
            "counts": {
                "master_plan_versions": len(versions),
                "rounds_pending_attribution": len(pending),
                "foreign_key_violations": len(fk_errors),
                "effective_master_versions": effective_count,
            },
        }

    def attribute_legacy_round(
        self,
        round_id: int,
        master_plan_version_id: int,
        *,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any]:
        """Manually resolve a legacy round only with an explicit audit reason."""
        reviewer = str(reviewer or "").strip()
        reason = str(reason or "").strip()
        if not reviewer or not reason:
            raise PlanVersionError("人工归属必须记录审核人和原因")
        self._begin()
        try:
            round_row = self.conn.execute(
                """SELECT plan_signature,master_plan_version_id,plan_attribution_status
                   FROM spiral_rounds WHERE id=?""", (round_id,)
            ).fetchone()
            if round_row is None:
                raise PlanVersionError("轮次不存在")
            if round_row["master_plan_version_id"] is not None:
                raise PlanVersionError("轮次已经归属版本，禁止覆盖")
            if round_row["plan_attribution_status"] not in ("pending", "manual_review"):
                raise PlanVersionError("当前轮次不在待人工归属状态")
            version = self.conn.execute(
                "SELECT plan_signature FROM master_plan_versions WHERE id=?",
                (master_plan_version_id,),
            ).fetchone()
            if version is None:
                raise PlanVersionError("待归属的主计划版本不存在")
            old_signature = round_row["plan_signature"]
            if old_signature and old_signature != version["plan_signature"]:
                raise PlanVersionError("所选版本签名与轮次已有签名不一致")
            self.conn.execute(
                """UPDATE spiral_rounds
                   SET master_plan_version_id=?,plan_signature=?,plan_attribution_status='resolved'
                   WHERE id=? AND master_plan_version_id IS NULL""",
                (master_plan_version_id, version["plan_signature"], round_id),
            )
            self.conn.execute(
                """INSERT INTO user_override(
                    round_id,override_field,original_value,override_value,
                    reason,overridden_by,overridden_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (round_id, "legacy_plan_attribution",
                 json.dumps({"plan_signature": old_signature,
                             "status": round_row["plan_attribution_status"]}, ensure_ascii=False),
                 json.dumps({"master_plan_version_id": master_plan_version_id,
                             "plan_signature": version["plan_signature"]}, ensure_ascii=False),
                 reason[:1000], reviewer, _now()),
            )
            self.conn.commit()
            return dict(self.conn.execute(
                """SELECT id,master_plan_version_id,plan_signature,plan_attribution_status
                   FROM spiral_rounds WHERE id=?""", (round_id,)
            ).fetchone())
        except Exception:
            self.conn.rollback()
            raise

    def list_pending_round_attribution(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))
        return [dict(row) for row in self.conn.execute(
            """SELECT id,round_number,status,created_at,plan_signature,plan_attribution_status
               FROM spiral_rounds
               WHERE plan_attribution_status IN ('pending','manual_review')
               ORDER BY id LIMIT ?""", (limit,)
        ).fetchall()]
