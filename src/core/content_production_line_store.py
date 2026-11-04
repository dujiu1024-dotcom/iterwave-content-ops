"""Per-content production-line lifecycle and immutable context snapshot repository."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class ContentProductionLineError(ValueError):
    """Raised when a per-content production-line operation is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


class ContentProductionLineStore:
    """One task/content has one line; tenant DB isolation supplies account-group scope."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def available(conn: sqlite3.Connection) -> bool:
        return _table_exists(conn, "content_production_lines") and _table_exists(
            conn, "production_context_snapshots"
        )

    def attach_snapshot(self, task_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or not snapshot.get("fingerprint"):
            raise ContentProductionLineError("生产上下文快照缺少指纹")
        line = self.conn.execute(
            "SELECT * FROM content_production_lines WHERE production_task_id=?", (task_id,)
        ).fetchone()
        if line is None:
            raise ContentProductionLineError("内容生产线不存在")
        existing = self.conn.execute(
            "SELECT * FROM production_context_snapshots WHERE production_task_id=?", (task_id,)
        ).fetchone()
        if existing is not None:
            if existing["fingerprint"] != snapshot["fingerprint"]:
                raise ContentProductionLineError("任务已有不可变上下文快照，禁止覆盖")
            return dict(existing)
        context = snapshot["context"]
        waiver = snapshot.get("waiver")
        waiver_id = waiver.get("id") if isinstance(waiver, dict) else None
        snapshot_id = str(context.get("snapshot_id") or ("pcs-" + uuid.uuid4().hex))
        self.conn.execute(
            """INSERT INTO production_context_snapshots(
                id,content_production_line_id,production_task_id,round_id,
                master_plan_version_id,sub_plan_id,sub_plan_version_id,strategy_step_id,
                latest_review_ref,context_status,waiver_id,fingerprint,context_json,captured_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot_id, line["id"], task_id, int(line["round_id"]),
                context["plan_versions"].get("master_plan_version_id"),
                context["plan_versions"].get("sub_plan_id"),
                context["plan_versions"].get("sub_plan_version_id"),
                context["plan_versions"].get("strategy_step_id"),
                context.get("latest_review_ref"),
                snapshot.get("context_status", "complete"), waiver_id,
                snapshot["fingerprint"],
                json.dumps(context, ensure_ascii=False, sort_keys=True),
                snapshot["captured_at"],
            ),
        )
        if waiver_id:
            self.conn.execute(
                """UPDATE production_context_waivers SET consumed_task_id=?
                   WHERE id=? AND consumed_task_id IS NULL""",
                (task_id, waiver_id),
            )
            if self.conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise ContentProductionLineError("人工豁免已被其他任务消费，请重新确认")
        self.conn.execute(
            """INSERT INTO content_production_line_events(
                content_production_line_id,event_type,from_status,to_status,actor,reason,
                evidence_json,created_at
            ) VALUES(?, 'context_snapshot_created', 'pending', 'pending',
                     'system', '生产前经营上下文已固化',
                     ?, ?)""",
            (line["id"], json.dumps({"snapshot_id": snapshot_id, "fingerprint": snapshot["fingerprint"]}),
             snapshot["captured_at"]),
        )
        return self.get_for_task(task_id)

    def record_waiver(
        self, *, round_id: int, platform: str, waived_items: list[str],
        reason: str, actor: str, evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed_platforms = {"xhs", "douyin", "kuaishou", "wechat_channels"}
        if platform not in allowed_platforms:
            raise ContentProductionLineError("人工豁免必须绑定有效的平台")
        # Only fields that the current assembler can explicitly classify as
        # missing are waivable. A database read/parse failure is never waivable.
        allowed = {
            "north_star", "business_profile_snapshot", "confirmed_business_diagnosis",
            "recent_published_content",
        }
        if not isinstance(waived_items, list) or not waived_items:
            raise ContentProductionLineError("必须明确列出人工豁免的数据项")
        normalized = sorted(set(str(item) for item in waived_items))
        if not set(normalized).issubset(allowed):
            raise ContentProductionLineError("包含不支持的人工豁免项")
        if not str(reason or "").strip():
            raise ContentProductionLineError("人工豁免必须填写原因")
        if not isinstance(evidence, dict) or not evidence:
            raise ContentProductionLineError("人工豁免必须附带可追溯证据")
        if not str(actor or "").strip():
            raise ContentProductionLineError("人工豁免必须记录操作人")
        if self.conn.execute(
            "SELECT 1 FROM spiral_rounds WHERE id=? AND sub_plan_id IS NOT NULL", (round_id,)
        ).fetchone() is None:
            raise ContentProductionLineError("豁免必须绑定有效的子计划生产轮次")
        pending = self.conn.execute(
            """SELECT id FROM production_context_waivers
               WHERE round_id=? AND platform=? AND consumed_task_id IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (round_id, platform),
        ).fetchone()
        if pending:
            raise ContentProductionLineError("该轮次已有未消费的人工豁免")
        waiver_id = "pcw-" + uuid.uuid4().hex
        now = _now()
        self.conn.execute(
            """INSERT INTO production_context_waivers(
                id,round_id,platform,waived_items_json,reason,actor,evidence_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (waiver_id, round_id, platform, json.dumps(normalized),
             str(reason).strip()[:2000], str(actor or "operator")[:120],
             json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True), now),
        )
        self.conn.commit()
        return {
            "id": waiver_id, "round_id": round_id, "platform": platform,
            "waived_items": normalized, "reason": str(reason).strip(),
            "actor": str(actor or "operator"), "created_at": now,
        }

    def transition(
        self, line_id: str, *, event_type: str, actor: str,
        reason: str = "", evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        transitions = {
            "publish_verified": ("verification_pending", "published"),
            "closed": ("published", "closed"),
            "aborted": (None, "aborted"),
        }
        if event_type not in transitions:
            raise ContentProductionLineError("不支持的生产线外部事件")
        if evidence is not None and not isinstance(evidence, dict):
            raise ContentProductionLineError("生产线事件证据必须为对象")
        evidence = evidence or {}
        line = self.conn.execute(
            "SELECT * FROM content_production_lines WHERE id=?", (line_id,)
        ).fetchone()
        if line is None:
            raise ContentProductionLineError("内容生产线不存在")
        expected, target = transitions[event_type]
        if expected is not None and line["status"] != expected:
            raise ContentProductionLineError(
                f"事件 {event_type} 要求状态为 {expected}，当前为 {line['status']}"
            )
        if event_type == "publish_verified" and not (
            evidence and any(evidence.get(key) for key in ("published_url", "platform_content_id", "verification_id"))
        ):
            raise ContentProductionLineError("真实发布核验必须提供平台读回证据引用")
        if event_type == "closed" and not (
            evidence and evidence.get("verification_id") and evidence.get("review_ref")
        ):
            raise ContentProductionLineError("关闭生产线必须关联发布核验和复盘记录")
        now = _now()
        try:
            self.conn.execute(
                """UPDATE content_production_lines SET status=?,updated_at=?,
                   closed_at=CASE WHEN ? IN ('closed','aborted') THEN ? ELSE closed_at END
                   WHERE id=?""",
                (target, now, target, now, line_id),
            )
            self.conn.execute(
                """INSERT INTO content_production_line_events(
                    content_production_line_id,event_type,from_status,to_status,
                    actor,reason,evidence_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (line_id, event_type, line["status"], target,
                 str(actor or "system")[:120], str(reason or "")[:2000],
                 json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True), now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise ContentProductionLineError(str(exc)) from exc
        return self.get_line(line_id)

    def get_for_task(self, task_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT id FROM content_production_lines WHERE production_task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise ContentProductionLineError("内容生产线不存在")
        return self.get_line(str(row["id"]))

    def get_line(self, line_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT l.*,t.status AS task_status,t.attempt_count,t.max_attempts,
                      t.error_code,t.error_message,t.content_id AS task_content_id,
                      t.strategy_step_id,t.platform
               FROM content_production_lines l
               JOIN production_tasks t ON t.id=l.production_task_id
               WHERE l.id=?""",
            (line_id,),
        ).fetchone()
        if row is None:
            raise ContentProductionLineError("内容生产线不存在")
        result = dict(row)
        snapshot = self.conn.execute(
            "SELECT * FROM production_context_snapshots WHERE content_production_line_id=?",
            (line_id,),
        ).fetchone()
        if snapshot is not None:
            result["context_snapshot"] = dict(snapshot)
            result["context_snapshot"]["context"] = json.loads(snapshot["context_json"])
        else:
            result["context_snapshot"] = None
        result["events"] = [
            dict(item) for item in self.conn.execute(
                """SELECT * FROM content_production_line_events
                   WHERE content_production_line_id=? ORDER BY id""", (line_id,)
            ).fetchall()
        ]
        return result

    def list_lines(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        args: list[Any] = []
        where = ""
        if status:
            where = " WHERE l.status=?"
            args.append(status)
        rows = self.conn.execute(
            """SELECT l.id FROM content_production_lines l""" + where
            + " ORDER BY CASE WHEN l.status='legacy_mapped' THEN 1 ELSE 0 END,"
              " l.updated_at DESC,l.id DESC LIMIT ?",
            [*args, max(1, min(int(limit), 200))],
        ).fetchall()
        return [self.get_line(str(row["id"])) for row in rows]
