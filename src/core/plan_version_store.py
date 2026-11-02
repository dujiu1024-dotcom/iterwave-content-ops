"""Immutable persistence for master-plan and content-subplan versions.

The module deliberately uses the existing sqlite3 connection and existing tables.
Business orchestration/API handlers may call this store; it does not own publishing.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .plan_version import PlanVersionManager


class PlanVersionError(ValueError):
    """Raised when a plan-version transition violates the data contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(row) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


class PlanVersionStore:
    """Transactional version repository for a single tenant SQLite database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._signer = PlanVersionManager(None, conn)

    def _begin(self) -> None:
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")

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
            row = self.conn.execute(
                "SELECT * FROM master_plan_versions WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            self.conn.commit()
            return dict(row)
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
        # Restore is a new draft; historical/effective rows are never rewritten.
        return self.create_master_draft(
            snapshot, author=author, source="user",
            based_on_version_id=version_id, change_note=change_note,
        )

    def activate_master_version(
        self, version_id: int, *, reviewer: str
    ) -> dict[str, Any]:
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
            snapshot = json.loads(row["plan_snapshot"])
            signature = self._signer.compute_signature(snapshot)
            if signature != row["plan_signature"]:
                raise PlanVersionError("主计划快照与签名不一致，拒绝生效")

            self.conn.execute(
                """UPDATE master_plan_versions
                   SET status='archived',superseded_at=?
                   WHERE status='effective'""", (now,)
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

    def list_master_versions(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        rows = self.conn.execute(
            "SELECT * FROM master_plan_versions ORDER BY version_no DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def create_subplan_version(
        self,
        plan_id: str,
        snapshot: dict[str, Any],
        *,
        master_plan_version_id: Optional[int],
        author: str = "user",
        stages: Optional[list[dict[str, Any]]] = None,
        change_note: Optional[str] = None,
    ) -> dict[str, Any]:
        if self.conn.execute(
            "SELECT 1 FROM content_plans WHERE id=?", (plan_id,)
        ).fetchone() is None:
            raise PlanVersionError("子计划不存在")
        if master_plan_version_id is not None and self.conn.execute(
            "SELECT 1 FROM master_plan_versions WHERE id=?", (master_plan_version_id,)
        ).fetchone() is None:
            raise PlanVersionError("关联的主计划版本不存在")
        if not isinstance(snapshot, dict):
            raise PlanVersionError("子计划快照必须是对象")
        stages = stages or []
        if not isinstance(stages, list):
            raise PlanVersionError("阶段必须是数组")
        now = _now()
        self._begin()
        try:
            version_no = int(self.conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM content_plan_versions WHERE plan_id=?",
                (plan_id,),
            ).fetchone()[0])
            cur = self.conn.execute(
                """INSERT INTO content_plan_versions(
                    plan_id,version_no,master_plan_version_id,snapshot_json,
                    status,created_at,created_by,change_note
                ) VALUES(?,?,?,?,'draft',?,?,?)""",
                (plan_id, version_no, master_plan_version_id,
                 json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                 now, author, change_note),
            )
            version_id = int(cur.lastrowid)
            for index, stage in enumerate(stages, start=1):
                if not isinstance(stage, dict):
                    raise PlanVersionError("阶段条目必须是对象")
                title = str(stage.get("title") or "").strip()
                if not title:
                    raise PlanVersionError("阶段标题不能为空")
                self.conn.execute(
                    """INSERT INTO content_plan_stages(
                        content_plan_version_id,stage_order,title,target_count,
                        start_date,end_date,status,notes,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?, 'pending',?,?,?)""",
                    (version_id, int(stage.get("stage_order") or index), title,
                     int(stage.get("target_count") or 1), stage.get("start_date"),
                     stage.get("end_date"), str(stage.get("notes") or ""), now, now),
                )
            self.conn.commit()
            return dict(self.conn.execute(
                "SELECT * FROM content_plan_versions WHERE id=?", (version_id,)
            ).fetchone())
        except Exception:
            self.conn.rollback()
            raise

    def activate_subplan_version(self, version_id: int) -> dict[str, Any]:
        now = _now()
        self._begin()
        try:
            row = self.conn.execute(
                "SELECT * FROM content_plan_versions WHERE id=?", (version_id,)
            ).fetchone()
            if row is None:
                raise PlanVersionError("子计划版本不存在")
            if row["status"] != "draft":
                raise PlanVersionError("只有子计划草稿版本可以生效")
            self.conn.execute(
                """UPDATE content_plan_versions SET status='archived'
                   WHERE plan_id=? AND status='effective'""", (row["plan_id"],)
            )
            self.conn.execute(
                """UPDATE content_plan_versions SET status='effective',effective_at=?
                   WHERE id=? AND status='draft'""", (now, version_id)
            )
            self.conn.execute(
                """UPDATE content_plans SET version=?,master_plan_version_id=?,updated_at=?
                   WHERE id=?""",
                (row["version_no"], row["master_plan_version_id"], now, row["plan_id"]),
            )
            self.conn.commit()
            return dict(self.conn.execute(
                "SELECT * FROM content_plan_versions WHERE id=?", (version_id,)
            ).fetchone())
        except Exception:
            self.conn.rollback()
            raise

    def bind_round(
        self,
        round_id: int,
        *,
        master_plan_version_id: int,
        content_plan_version_id: Optional[int] = None,
        content_plan_stage_id: Optional[int] = None,
    ) -> None:
        """Persist the exact versions consumed by a round; never infer from current settings."""
        master = self.conn.execute(
            "SELECT plan_signature FROM master_plan_versions WHERE id=?",
            (master_plan_version_id,),
        ).fetchone()
        if master is None:
            raise PlanVersionError("轮次绑定的主计划版本不存在")
        if content_plan_stage_id is not None:
            if content_plan_version_id is None:
                raise PlanVersionError("指定阶段时必须同时指定子计划版本")
            stage = self.conn.execute(
                """SELECT 1 FROM content_plan_stages
                   WHERE id=? AND content_plan_version_id=?""",
                (content_plan_stage_id, content_plan_version_id),
            ).fetchone()
            if stage is None:
                raise PlanVersionError("阶段不属于指定的子计划版本")
        elif content_plan_version_id is not None and self.conn.execute(
            "SELECT 1 FROM content_plan_versions WHERE id=?",
            (content_plan_version_id,),
        ).fetchone() is None:
            raise PlanVersionError("轮次绑定的子计划版本不存在")
        cur = self.conn.execute(
            """UPDATE spiral_rounds SET master_plan_version_id=?,
                   content_plan_version_id=?,content_plan_stage_id=?,
                   plan_signature=?,plan_attribution_status='resolved'
               WHERE id=?""",
            (master_plan_version_id, content_plan_version_id, content_plan_stage_id,
             master["plan_signature"], round_id),
        )
        if cur.rowcount != 1:
            raise PlanVersionError("轮次不存在")
        self.conn.commit()

    def list_pending_round_attribution(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))
        rows = self.conn.execute(
            """SELECT id,round_number,status,created_at,plan_signature
               FROM spiral_rounds WHERE plan_attribution_status IN ('pending','manual_review')
               ORDER BY id LIMIT ?""", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
