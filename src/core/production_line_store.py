"""Versioned production-line lifecycle bound to approved platform subplans."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class ProductionLineError(ValueError):
    """Raised when a production-line lifecycle transition is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProductionLineStore:
    """Tenant-local persistence for one logical production line per subplan."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _line(self, line_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT l.*,p.platform,p.title AS subplan_title,
                      p.current_version_id AS current_subplan_version_id,
                      v.version_no AS current_version_no,
                      v.status AS current_version_status,
                      v.sub_plan_version_id
               FROM production_lines l
               JOIN sub_plans p ON p.id=l.sub_plan_id
               LEFT JOIN production_line_versions v ON v.id=l.current_version_id
               WHERE l.id=?""",
            (line_id,),
        ).fetchone()
        if row is None:
            raise ProductionLineError("生产线不存在")
        result = dict(row)
        result["is_stale"] = (
            result["sub_plan_version_id"] is not None
            and result["sub_plan_version_id"] != result["current_subplan_version_id"]
        )
        return result

    def _validate_definition(
        self, sub_plan_version_id: int, definition: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(definition, dict):
            raise ProductionLineError("生产线定义必须是对象")
        title = str(definition.get("title") or "").strip()
        if not title or len(title) > 160:
            raise ProductionLineError("生产线名称必须为 1-160 个字符")
        stages = definition.get("stages")
        if not isinstance(stages, list) or not stages:
            raise ProductionLineError("生产线至少需要绑定一个策略步骤")
        source_rows = self.conn.execute(
            """SELECT stage_key,stage_order,title,target_count,dependencies_json,status
               FROM sub_plan_stages WHERE sub_plan_version_id=? ORDER BY stage_order,id""",
            (sub_plan_version_id,),
        ).fetchall()
        if not source_rows:
            raise ProductionLineError("子计划版本没有可绑定的策略步骤")
        source_order = [str(row["stage_key"]) for row in source_rows]
        source = {str(row["stage_key"]): dict(row) for row in source_rows}
        seen: set[str] = set()
        normalized = []
        requested_order = []
        for index, raw in enumerate(stages, 1):
            if not isinstance(raw, dict):
                raise ProductionLineError("生产线步骤必须是对象")
            key = str(raw.get("stage_key") or "").strip()
            if not key or key not in source:
                raise ProductionLineError("生产线步骤必须引用当前子计划版本中的策略步骤")
            if key in seen:
                raise ProductionLineError("生产线不能重复绑定同一个策略步骤")
            seen.add(key)
            requested_order.append(key)
            batch_size = raw.get("batch_size", source[key]["target_count"])
            if isinstance(batch_size, bool):
                raise ProductionLineError("每批生产数量必须是正整数")
            raw_batch_size = batch_size
            try:
                batch_size = int(raw_batch_size)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProductionLineError("每批生产数量必须是正整数") from exc
            if str(raw_batch_size).strip() != str(batch_size):
                raise ProductionLineError("每批生产数量必须是正整数")
            if batch_size < 1 or batch_size > 1000:
                raise ProductionLineError("每批生产数量必须在 1-1000 之间")
            if batch_size > int(source[key]["target_count"]):
                raise ProductionLineError("每批生产数量不能超过对应策略步骤的目标篇数")
            normalized.append({
                "stage_key": key,
                "stage_order": index,
                "stage_title": str(source[key]["title"]),
                "batch_size": batch_size,
                "production_goal": str(raw.get("production_goal") or source[key]["title"])[:2000],
                "instructions": str(raw.get("instructions") or "")[:8000],
                "cadence": str(raw.get("cadence") or "")[:500],
            })
        # The line must preserve the full strategy chain; silently dropping a step
        # would let the executor bypass the approved subplan.
        if seen != set(source):
            raise ProductionLineError("生产线必须覆盖当前子计划版本的全部策略步骤")
        if requested_order != source_order:
            raise ProductionLineError("生产线步骤必须遵循子计划策略步骤的既定顺序")
        execution_mode = str(definition.get("execution_mode") or "sequential")
        if execution_mode != "sequential":
            raise ProductionLineError("当前仅支持按策略步骤顺序执行生产线")
        return {
            "schema_version": 1,
            "title": title,
            "description": str(definition.get("description") or "")[:4000],
            "execution_mode": execution_mode,
            "stages": normalized,
        }

    def create_draft(
        self,
        sub_plan_id: str,
        sub_plan_version_id: int,
        definition: dict[str, Any],
        *,
        generation_source: str = "manual",
        author: str = "user",
        change_note: str = "创建生产线草稿",
        based_on_version_id: int | None = None,
    ) -> dict[str, Any]:
        if generation_source not in {"ai", "manual", "system"}:
            raise ProductionLineError("生产线来源无效")
        now = _now()
        owns_transaction = not self.conn.in_transaction
        savepoint = "production_line_" + uuid.uuid4().hex
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            subplan = self.conn.execute(
                """SELECT p.*,v.status AS version_status,v.version_no,
                          v.master_plan_version_id,m.status AS master_status
                   FROM sub_plans p
                   JOIN sub_plan_versions v ON v.id=?
                   JOIN master_plan_versions m ON m.id=v.master_plan_version_id
                   WHERE p.id=? AND v.sub_plan_id=p.id""",
                (sub_plan_version_id, sub_plan_id),
            ).fetchone()
            if subplan is None:
                raise ProductionLineError("子计划版本不属于该子计划")
            if subplan["status"] != "active" or subplan["current_version_id"] != sub_plan_version_id:
                raise ProductionLineError("只有当前已审核生效的子计划才能创建生产线")
            if subplan["version_status"] != "effective" or subplan["master_status"] != "effective":
                raise ProductionLineError("子计划及其母计划必须均处于生效状态")
            normalized = self._validate_definition(sub_plan_version_id, definition)
            line_row = self.conn.execute(
                "SELECT * FROM production_lines WHERE sub_plan_id=?", (sub_plan_id,)
            ).fetchone()
            if line_row is None:
                line_id = "line_" + uuid.uuid4().hex
                self.conn.execute(
                    """INSERT INTO production_lines(id,sub_plan_id,status,created_at,updated_at,created_by)
                       VALUES(?,?,'draft',?,?,?)""",
                    (line_id, sub_plan_id, now, now, author),
                )
            else:
                line_id = str(line_row["id"])
                if line_row["status"] in {"completed", "cancelled", "archived"}:
                    raise ProductionLineError("已终止的生产线不能直接创建新版本")
                current_version_id = (
                    int(line_row["current_version_id"])
                    if line_row["current_version_id"] is not None else None
                )
                if based_on_version_id is None and current_version_id is not None:
                    based_on_version_id = current_version_id
                elif based_on_version_id != current_version_id:
                    raise ProductionLineError("生产线基准版本已过期，请刷新当前版本后再编辑")
            if based_on_version_id is not None:
                base = self.conn.execute(
                    "SELECT production_line_id FROM production_line_versions WHERE id=?",
                    (based_on_version_id,),
                ).fetchone()
                if base is None or base["production_line_id"] != line_id:
                    raise ProductionLineError("基准生产线版本不属于当前生产线")
            version_no = int(self.conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM production_line_versions WHERE production_line_id=?",
                (line_id,),
            ).fetchone()[0])
            cur = self.conn.execute(
                """INSERT INTO production_line_versions(
                       production_line_id,sub_plan_version_id,version_no,status,definition_json,
                       generation_source,based_on_version_id,created_at,created_by,change_note
                   ) VALUES(?,?,?,'draft',?,?,?,?,?,?)""",
                (line_id, sub_plan_version_id, version_no,
                 json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                 generation_source, based_on_version_id, now, author, change_note),
            )
            self.conn.execute("UPDATE production_lines SET updated_at=? WHERE id=?", (now, line_id))
            if owns_transaction:
                self.conn.commit()
            else:
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return self.get_version(int(cur.lastrowid))
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            else:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def get_version(self, version_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT v.*,l.sub_plan_id,l.status AS line_status,l.current_version_id,
                      p.platform,p.title AS subplan_title
               FROM production_line_versions v
               JOIN production_lines l ON l.id=v.production_line_id
               JOIN sub_plans p ON p.id=l.sub_plan_id WHERE v.id=?""",
            (version_id,),
        ).fetchone()
        if row is None:
            raise ProductionLineError("生产线版本不存在")
        result = dict(row)
        try:
            result["definition"] = json.loads(result.pop("definition_json"))
        except (TypeError, ValueError) as exc:
            raise ProductionLineError("生产线定义数据损坏") from exc
        return result

    def get_for_subplan(self, sub_plan_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id FROM production_lines WHERE sub_plan_id=?", (sub_plan_id,)
        ).fetchone()
        if row is None:
            return None
        line = self._line(str(row["id"]))
        versions = [
            self.get_version(int(item["id"]))
            for item in self.conn.execute(
                "SELECT id FROM production_line_versions WHERE production_line_id=? ORDER BY version_no DESC",
                (line["id"],),
            ).fetchall()
        ]
        line["versions"] = versions
        return line

    def activate(self, version_id: int, *, reviewer: str = "user") -> dict[str, Any]:
        now = _now()
        owns_transaction = not self.conn.in_transaction
        savepoint = "production_line_" + uuid.uuid4().hex
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            version = self.conn.execute(
                """SELECT v.*,l.sub_plan_id,l.status AS line_status,l.current_version_id,
                          p.status AS subplan_status,p.current_version_id AS current_subplan_version_id,
                          m.status AS master_status
                   FROM production_line_versions v
                   JOIN production_lines l ON l.id=v.production_line_id
                   JOIN sub_plans p ON p.id=l.sub_plan_id
                   JOIN master_plan_versions m ON m.id=p.master_plan_version_id
                   WHERE v.id=?""",
                (version_id,),
            ).fetchone()
            if version is None:
                raise ProductionLineError("生产线版本不存在")
            if version["status"] != "draft":
                raise ProductionLineError("只有草稿生产线版本可以确认")
            if version["line_status"] in {"completed", "cancelled", "archived"}:
                raise ProductionLineError("已终止的生产线不能重新确认")
            if version["based_on_version_id"] != version["current_version_id"]:
                raise ProductionLineError("生产线基准版本已过期，当前草稿不能覆盖更新版本")
            if version["subplan_status"] != "active" or version["master_status"] != "effective":
                raise ProductionLineError("子计划及其母计划必须保持生效")
            if version["sub_plan_version_id"] != version["current_subplan_version_id"]:
                raise ProductionLineError("生产线绑定的子计划版本已过期，请重新生成或修订")
            self.conn.execute(
                "UPDATE production_line_versions SET status='archived' WHERE production_line_id=? AND status='effective'",
                (version["production_line_id"],),
            )
            self.conn.execute(
                """UPDATE production_line_versions SET status='effective',reviewed_at=?,reviewed_by=?
                   WHERE id=? AND status='draft'""",
                (now, reviewer, version_id),
            )
            # Approving a new immutable version must not silently undo an
            # operator's explicit pause. Resume remains a separate transition.
            next_line_status = "paused" if version["line_status"] == "paused" else "active"
            self.conn.execute(
                """UPDATE production_lines SET current_version_id=?,status=?,
                       updated_at=?,activated_at=?,activated_by=?,
                       pause_reason=CASE WHEN ?='paused' THEN pause_reason ELSE NULL END
                   WHERE id=?""",
                (version_id, next_line_status, now, now, reviewer,
                 next_line_status, version["production_line_id"]),
            )
            if owns_transaction:
                self.conn.commit()
            else:
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return self.get_version(version_id)
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            else:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def reject(self, version_id: int, *, reviewer: str = "user", reason: str = "") -> dict[str, Any]:
        owns_transaction = not self.conn.in_transaction
        now = _now()
        row = self.conn.execute(
            "SELECT status FROM production_line_versions WHERE id=?", (version_id,)
        ).fetchone()
        if row is None:
            raise ProductionLineError("生产线版本不存在")
        if row["status"] != "draft":
            raise ProductionLineError("只有草稿生产线版本可以拒绝")
        cursor = self.conn.execute(
            "UPDATE production_line_versions SET status='rejected',reviewed_at=?,reviewed_by=?,change_note=? WHERE id=? AND status='draft'",
            (now, reviewer, (reason or "人工拒绝")[:2000], version_id),
        )
        if cursor.rowcount != 1:
            raise ProductionLineError("生产线草稿状态已变化，请刷新后重试")
        if owns_transaction:
            self.conn.commit()
        return self.get_version(version_id)

    def set_status(self, sub_plan_id: str, status: str, *, actor: str = "user", reason: str = "") -> dict[str, Any]:
        owns_transaction = not self.conn.in_transaction
        if status not in {"paused", "active", "cancelled"}:
            raise ProductionLineError("不支持的生产线状态迁移")
        line_row = self.conn.execute(
            "SELECT * FROM production_lines WHERE sub_plan_id=?", (sub_plan_id,)
        ).fetchone()
        if line_row is None:
            raise ProductionLineError("生产线不存在")
        current_status = str(line_row["status"])
        allowed = {
            "active": {"paused", "cancelled"},
            "paused": {"active", "cancelled"},
            "cancelled": set(),
            "completed": set(),
            "archived": set(),
            "draft": set(),
        }
        if status not in allowed.get(current_status, set()):
            raise ProductionLineError(f"生产线不能从 {current_status} 转换为 {status}")
        if status == "active":
            parent = self.conn.execute(
                """SELECT p.status AS subplan_status,p.current_version_id,
                          m.status AS master_status,v.status AS version_status,
                          v.sub_plan_version_id
                   FROM sub_plans p JOIN master_plan_versions m ON m.id=p.master_plan_version_id
                   JOIN production_line_versions v ON v.id=? WHERE p.id=?""",
                (line_row["current_version_id"], sub_plan_id),
            ).fetchone()
            if (parent is None or parent["subplan_status"] != "active"
                    or parent["master_status"] != "effective"
                    or parent["version_status"] != "effective"
                    or parent["sub_plan_version_id"] != parent["current_version_id"]):
                raise ProductionLineError("母计划、子计划及其绑定生产线版本均生效时才能恢复生产")
        cursor = self.conn.execute(
            """UPDATE production_lines SET status=?,updated_at=?,pause_reason=?
               WHERE id=? AND status=?""",
            (status, _now(), (reason or None) if status == "paused" else None,
             line_row["id"], current_status),
        )
        if cursor.rowcount != 1:
            raise ProductionLineError("生产线状态已被其他操作修改，请刷新后重试")
        if owns_transaction:
            self.conn.commit()
        return self._line(str(line_row["id"]))

    def list_lines(self, *, status: str | None = None) -> list[dict[str, Any]]:
        where = " WHERE l.status=?" if status else ""
        args = (status,) if status else ()
        rows = self.conn.execute(
            """SELECT l.*,p.platform,p.title AS subplan_title,
                      p.current_version_id AS current_subplan_version_id,
                      v.version_no AS current_version_no,v.status AS current_version_status,
                      v.sub_plan_version_id
               FROM production_lines l JOIN sub_plans p ON p.id=l.sub_plan_id
               LEFT JOIN production_line_versions v ON v.id=l.current_version_id"""
            + where + " ORDER BY l.updated_at DESC,l.id",
            args,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["is_stale"] = (
                item["sub_plan_version_id"] is not None
                and item["sub_plan_version_id"] != item["current_subplan_version_id"]
            )
            result.append(item)
        return result
