"""Version-bound subgoal tree, dependency gates and auditable acceptance."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class SubGoalError(ValueError):
    pass


GOAL_TYPES = {"quantity", "business", "effect", "quality", "capability"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SubGoalStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _goal(self, goal_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM sub_goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise SubGoalError("子目标不存在")
        return dict(row)

    def _assert_version(self, version_id: int, sub_plan_id: str, *, draft: bool = False) -> dict:
        row = self.conn.execute(
            """SELECT v.*,p.current_version_id FROM sub_plan_versions v
               JOIN sub_plans p ON p.id=v.sub_plan_id
               WHERE v.id=? AND v.sub_plan_id=?""", (version_id, sub_plan_id)
        ).fetchone()
        if row is None:
            raise SubGoalError("子目标版本不属于指定平台子计划")
        if draft and row["status"] != "draft":
            raise SubGoalError("只能在子计划草稿版本中编辑子目标")
        return dict(row)

    def _dependencies(self, goal_id: str) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT depends_on_goal_id FROM sub_goal_dependencies WHERE goal_id=? ORDER BY depends_on_goal_id",
            (goal_id,)
        ).fetchall()]

    def _validate_graph(self, version_id: int) -> None:
        rows = self.conn.execute(
            """SELECT d.goal_id,d.depends_on_goal_id
               FROM sub_goal_dependencies d JOIN sub_goals g ON g.id=d.goal_id
               WHERE g.sub_plan_version_id=?""", (version_id,)
        ).fetchall()
        graph: dict[str, list[str]] = {}
        for row in rows:
            left, right = row["goal_id"], row["depends_on_goal_id"]
            a = self._goal(left)
            b = self._goal(right)
            if a["sub_plan_version_id"] != b["sub_plan_version_id"]:
                raise SubGoalError("子目标依赖只能指向同一子计划版本")
            graph.setdefault(left, []).append(right)
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise SubGoalError("子目标依赖关系存在循环")
            if node in visited:
                return
            visiting.add(node)
            for predecessor in graph.get(node, []):
                visit(predecessor)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)

        parent_rows = self.conn.execute(
            "SELECT id,parent_goal_id FROM sub_goals WHERE sub_plan_version_id=?", (version_id,)
        ).fetchall()
        parent_graph = {row["id"]: row["parent_goal_id"] for row in parent_rows}
        for node in parent_graph:
            seen: set[str] = set()
            cursor = node
            while cursor is not None:
                if cursor in seen:
                    raise SubGoalError("子目标父子层级存在循环")
                seen.add(cursor)
                cursor = parent_graph.get(cursor)

    def _refresh(self, version_id: int) -> None:
        rows = self.conn.execute(
            "SELECT id,status FROM sub_goals WHERE sub_plan_version_id=?", (version_id,)
        ).fetchall()
        for row in rows:
            if row["status"] in {"completed", "cancelled", "in_progress", "failed", "needs_review"}:
                continue
            deps = self._dependencies(row["id"])
            passed = bool(deps) and all(
                self.conn.execute("SELECT status FROM sub_goals WHERE id=?", (dep,)).fetchone()[0] == "completed"
                for dep in deps
            )
            status = "ready" if not deps or passed else "blocked"
            self.conn.execute(
                "UPDATE sub_goals SET status=?,updated_at=? WHERE id=?",
                (status, _now(), row["id"])
            )

    def list_goals(self, sub_plan_id: str, version_id: int) -> list[dict[str, Any]]:
        self._assert_version(version_id, sub_plan_id)
        result = []
        for row in self.conn.execute(
            "SELECT * FROM sub_goals WHERE sub_plan_version_id=? ORDER BY goal_order,id", (version_id,)
        ).fetchall():
            item = dict(row)
            item["dependencies"] = self._dependencies(item["id"])
            plan = self.conn.execute(
                "SELECT * FROM sub_goal_production_plans WHERE goal_id=?", (item["id"],)
            ).fetchone()
            if plan:
                item["production_plan"] = dict(plan)
                try:
                    item["production_plan"]["resource_requirements"] = json.loads(plan["resource_requirements_json"])
                except (TypeError, ValueError):
                    item["production_plan"]["resource_requirements"] = {}
            else:
                item["production_plan"] = None
            latest = self.conn.execute(
                "SELECT * FROM sub_goal_evidence WHERE goal_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (item["id"],)
            ).fetchone()
            item["latest_evidence"] = dict(latest) if latest else None
            if item["latest_evidence"]:
                try:
                    item["latest_evidence"]["evidence"] = json.loads(item["latest_evidence"].pop("evidence_json"))
                except (TypeError, ValueError):
                    item["latest_evidence"]["evidence"] = {}
            item["evidence_history"] = []
            for evidence_row in self.conn.execute(
                "SELECT * FROM sub_goal_evidence WHERE goal_id=? ORDER BY created_at DESC,id DESC",
                (item["id"],)
            ).fetchall():
                proof = dict(evidence_row)
                try:
                    proof["evidence"] = json.loads(proof.pop("evidence_json"))
                except (TypeError, ValueError):
                    proof["evidence"] = {}
                item["evidence_history"].append(proof)
            result.append(item)
        return result

    def create_goal(self, sub_plan_id: str, version_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._assert_version(version_id, sub_plan_id, draft=True)
        goal_type = str(payload.get("goal_type") or "")
        title = str(payload.get("title") or "").strip()
        criteria = str(payload.get("acceptance_criteria") or "").strip()
        if goal_type not in GOAL_TYPES:
            raise SubGoalError("目标类型无效")
        if not title or len(title) > 200 or not criteria:
            raise SubGoalError("目标标题（1-200 字）和验收条件不能为空")
        order = int(payload.get("goal_order") or 1)
        priority = int(payload.get("priority") or 3)
        if order < 1 or priority not in range(1, 6):
            raise SubGoalError("目标排序必须大于零，优先级为 1-5")
        target_count = payload.get("target_count")
        if goal_type == "quantity" and target_count is None:
            raise SubGoalError("数量目标必须提供非负目标数量")
        if target_count is not None:
            if isinstance(target_count, bool):
                raise SubGoalError("目标数量必须是非负整数")
            try:
                target_count = int(target_count)
            except (TypeError, ValueError, OverflowError) as exc:
                raise SubGoalError("目标数量必须是非负整数") from exc
            if target_count < 0:
                raise SubGoalError("目标数量必须是非负整数")
        stage_id = payload.get("stage_id")
        if stage_id is not None and not self.conn.execute(
            "SELECT 1 FROM sub_plan_stages WHERE id=? AND sub_plan_version_id=?",
            (stage_id, version_id)
        ).fetchone():
            raise SubGoalError("关联阶段不属于指定子计划版本")
        if payload.get("requires_production") and stage_id is None:
            raise SubGoalError("需要内容生产的子目标必须关联策略阶段")
        parent_id = payload.get("parent_goal_id")
        if parent_id:
            parent = self._goal(str(parent_id))
            if parent["sub_plan_version_id"] != version_id:
                raise SubGoalError("父目标必须属于同一子计划版本")
        deps = payload.get("dependencies") or []
        if not isinstance(deps, list) or len(set(deps)) != len(deps):
            raise SubGoalError("依赖必须是无重复的目标 ID 数组")
        goal_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """INSERT INTO sub_goals(id,sub_plan_version_id,stage_id,parent_goal_id,goal_type,
                   goal_order,priority,title,description,target_count,acceptance_criteria,
                   requires_production,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (goal_id, version_id, stage_id, parent_id, goal_type, order, priority, title,
                 str(payload.get("description") or ""), target_count, criteria,
                 1 if payload.get("requires_production") else 0, "ready", now, now)
            )
            for dep in deps:
                predecessor = self._goal(str(dep))
                if predecessor["sub_plan_version_id"] != version_id:
                    raise SubGoalError("目标依赖只能指向同一子计划版本")
                self.conn.execute(
                    "INSERT INTO sub_goal_dependencies(goal_id,depends_on_goal_id,created_at) VALUES(?,?,?)",
                    (goal_id, str(dep), now)
                )
            self._validate_graph(version_id)
            self._refresh(version_id)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self._goal(goal_id)

    def update_goal(self, sub_plan_id: str, goal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        goal = self._goal(goal_id)
        version = self._assert_version(goal["sub_plan_version_id"], sub_plan_id, draft=True)
        if goal["status"] == "completed":
            raise SubGoalError("已完成目标不可原地修改；请在新子计划版本中重新定义")
        fields = {
            "title": str(payload.get("title", goal["title"])).strip(),
            "description": str(payload.get("description", goal["description"])),
            "acceptance_criteria": str(payload.get("acceptance_criteria", goal["acceptance_criteria"])).strip(),
            "goal_type": str(payload.get("goal_type", goal["goal_type"])),
            "priority": int(payload.get("priority", goal["priority"])),
            "goal_order": int(payload.get("goal_order", goal["goal_order"])),
            "target_count": payload.get("target_count", goal["target_count"]),
            "stage_id": payload.get("stage_id", goal["stage_id"]),
            "parent_goal_id": payload.get("parent_goal_id", goal["parent_goal_id"]),
            "requires_production": 1 if payload.get("requires_production", goal["requires_production"]) else 0,
        }
        if not fields["title"] or not fields["acceptance_criteria"] or fields["priority"] not in range(1,6):
            raise SubGoalError("标题、验收条件不能为空，优先级为 1-5")
        if fields["goal_type"] not in GOAL_TYPES:
            raise SubGoalError("目标类型无效")
        if fields["goal_type"] == "quantity":
            if fields["target_count"] is None or isinstance(fields["target_count"], bool):
                raise SubGoalError("数量目标必须提供非负目标数量")
            fields["target_count"] = int(fields["target_count"])
            if fields["target_count"] < 0:
                raise SubGoalError("数量目标必须提供非负目标数量")
        if fields["requires_production"] and fields["stage_id"] is None:
            raise SubGoalError("需要内容生产的子目标必须关联策略阶段")
        if fields["stage_id"] is not None and not self.conn.execute(
            "SELECT 1 FROM sub_plan_stages WHERE id=? AND sub_plan_version_id=?",
            (fields["stage_id"], version["id"])
        ).fetchone():
            raise SubGoalError("关联阶段不属于指定子计划版本")
        if fields["parent_goal_id"]:
            parent = self._goal(str(fields["parent_goal_id"]))
            if parent["sub_plan_version_id"] != version["id"] or parent["id"] == goal_id:
                raise SubGoalError("父目标必须是同一版本内的其他目标")
        deps = payload.get("dependencies", self._dependencies(goal_id))
        if not isinstance(deps, list) or len(set(deps)) != len(deps):
            raise SubGoalError("依赖必须是无重复的目标 ID 数组")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old_order = int(goal["goal_order"])
            new_order = int(fields["goal_order"])
            if new_order < 1:
                raise SubGoalError("目标顺序必须为正整数")
            if new_order != old_order:
                ordered_ids = [
                    str(row["id"]) for row in self.conn.execute(
                        "SELECT id FROM sub_goals WHERE sub_plan_version_id=? ORDER BY goal_order,id",
                        (version["id"],),
                    ).fetchall()
                ]
                if goal_id not in ordered_ids:
                    raise SubGoalError("子目标不属于指定子计划版本")
                ordered_ids.remove(goal_id)
                target_order = min(new_order, len(ordered_ids) + 1)
                ordered_ids.insert(target_order - 1, goal_id)
                # Move every row out of the live positive range first. Updating only
                # the intervening rows can collide with SQLite's immediate UNIQUE check.
                self.conn.execute(
                    "UPDATE sub_goals SET goal_order=goal_order+1000000 WHERE sub_plan_version_id=?",
                    (version["id"],),
                )
                for position, ordered_goal_id in enumerate(ordered_ids, start=1):
                    self.conn.execute(
                        "UPDATE sub_goals SET goal_order=? WHERE id=?",
                        (position, ordered_goal_id),
                    )
                fields["goal_order"] = target_order
            self.conn.execute(
                """UPDATE sub_goals SET title=?,description=?,acceptance_criteria=?,goal_type=?,priority=?,
                   goal_order=?,target_count=?,stage_id=?,parent_goal_id=?,requires_production=?,
                   status=CASE WHEN status IN ('failed','needs_review') THEN 'ready' ELSE status END,
                   updated_at=? WHERE id=?""",
                (*fields.values(), _now(), goal_id)
            )
            self.conn.execute("DELETE FROM sub_goal_dependencies WHERE goal_id=?", (goal_id,))
            for dep in deps:
                predecessor = self._goal(str(dep))
                if predecessor["sub_plan_version_id"] != version["id"]:
                    raise SubGoalError("目标依赖只能指向同一子计划版本")
                self.conn.execute(
                    "INSERT INTO sub_goal_dependencies(goal_id,depends_on_goal_id,created_at) VALUES(?,?,?)",
                    (goal_id, str(dep), _now())
                )
            self._validate_graph(int(version["id"]))
            self._refresh(int(version["id"]))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self._goal(goal_id)

    def save_production_plan(self, sub_plan_id: str, goal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        goal = self._goal(goal_id)
        version = self._assert_version(goal["sub_plan_version_id"], sub_plan_id)
        if not (
            version["status"] == "draft"
            or (version["status"] == "effective" and int(version["current_version_id"] or 0) == int(version["id"]))
        ):
            raise SubGoalError("只能维护草稿或当前生效子计划版本的生产计划")
        if goal["stage_id"] is None:
            raise SubGoalError("生产计划必须关联一个策略阶段")
        if goal["status"] == "completed":
            raise SubGoalError("已完成目标的生产计划不可修改")
        direction = str(payload.get("content_direction") or "").strip()
        conditions = str(payload.get("execution_conditions") or "").strip()
        acceptance = str(payload.get("acceptance_relation") or "").strip()
        count = int(payload.get("suggested_count") or 1)
        priority = int(payload.get("priority") or goal["priority"])
        resources = payload.get("resource_requirements") or {}
        if count < 1 or count > 10000 or priority not in range(1,6) or not isinstance(resources, dict):
            raise SubGoalError("生产计划数量、优先级或资源需求格式无效")
        if not direction or not conditions or not acceptance:
            raise SubGoalError("生产方向、执行条件和验收关联均不能为空")
        now = _now()
        self.conn.execute(
            """INSERT INTO sub_goal_production_plans(
                 goal_id,content_direction,suggested_count,resource_requirements_json,
                 priority,execution_conditions,acceptance_relation,enabled,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,1,?,?)
               ON CONFLICT(goal_id) DO UPDATE SET content_direction=excluded.content_direction,
                 suggested_count=excluded.suggested_count,
                 resource_requirements_json=excluded.resource_requirements_json,
                 priority=excluded.priority,execution_conditions=excluded.execution_conditions,
                 acceptance_relation=excluded.acceptance_relation,enabled=1,updated_at=excluded.updated_at""",
            (goal_id,direction,count,_json(resources),priority,conditions,acceptance,now,now)
        )
        self.conn.execute("UPDATE sub_goals SET requires_production=1,updated_at=? WHERE id=?", (now,goal_id))
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM sub_goal_production_plans WHERE goal_id=?", (goal_id,)).fetchone())

    def accept(self, sub_plan_id: str, goal_id: str, payload: dict[str, Any], reviewer: str = "user") -> dict[str, Any]:
        goal = self._goal(goal_id)
        version = self._assert_version(goal["sub_plan_version_id"], sub_plan_id)
        if version["status"] != "effective" or int(version["current_version_id"] or 0) != int(version["id"]):
            raise SubGoalError("只能验收当前生效子计划版本中的目标")
        if goal["status"] in {"completed", "cancelled"}:
            raise SubGoalError("目标已完成或取消，不能重复验收")
        unmet = self.conn.execute(
            """SELECT g.title FROM sub_goal_dependencies d
               JOIN sub_goals g ON g.id=d.depends_on_goal_id
               WHERE d.goal_id=? AND g.status!='completed'""", (goal_id,)
        ).fetchall()
        if unmet:
            raise SubGoalError("前置子目标尚未验收通过：" + "、".join(row["title"] for row in unmet))
        decision = str(payload.get("decision") or "")
        reason = str(payload.get("reason") or "").strip()
        evidence = payload.get("evidence") or {}
        if decision not in {"passed", "failed", "needs_review"} or not isinstance(evidence, dict) or not evidence:
            raise SubGoalError("验收结论无效，且必须提交证据对象")
        if decision != "passed" and not reason:
            raise SubGoalError("未通过或待复核时必须填写原因")
        actual_count = payload.get("actual_count")
        if goal["goal_type"] == "quantity":
            if actual_count is None or isinstance(actual_count, bool):
                raise SubGoalError("数量目标验收必须提供实际完成数量")
            actual_count = int(actual_count)
            if actual_count < 0:
                raise SubGoalError("实际完成数量不能为负数")
            if decision == "passed" and (goal["target_count"] is None or actual_count < int(goal["target_count"])):
                raise SubGoalError("实际完成数量未达到目标值，不能验收通过")
        now = _now()
        status = {"passed":"completed","failed":"failed","needs_review":"needs_review"}[decision]
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """INSERT INTO sub_goal_evidence(goal_id,decision,reason,evidence_json,actual_count,reviewed_by,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (goal_id,decision,reason,_json(evidence),actual_count,reviewer,now)
            )
            self.conn.execute(
                "UPDATE sub_goals SET status=?,completed_at=?,updated_at=? WHERE id=?",
                (status, now if status == "completed" else None, now, goal_id)
            )
            self._refresh(int(goal["sub_plan_version_id"]))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self._goal(goal_id)


def assert_stage_goals_ready(conn: sqlite3.Connection, version_id: int, stage_id: int) -> None:
    """Validate all unfinished production goals associated with one stage."""
    for goal in stage_production_goals(conn, version_id, stage_id):
        if goal["status"] in {"completed", "cancelled"}:
            continue
        assert_goal_ready(conn, str(goal["id"]), version_id, stage_id)

def stage_production_goals(conn: sqlite3.Connection, version_id: int, stage_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id,title,status FROM sub_goals
           WHERE sub_plan_version_id=? AND stage_id=? AND requires_production=1
           ORDER BY goal_order,id""", (version_id, stage_id)
    ).fetchall()
    return [dict(row) for row in rows]


def assert_goal_ready(
    conn: sqlite3.Connection, goal_id: str, version_id: int, stage_id: int
) -> dict[str, Any]:
    row = conn.execute(
        """SELECT g.*,p.enabled AS plan_enabled,p.content_direction,p.suggested_count,
                  p.resource_requirements_json,p.priority AS plan_priority,
                  p.execution_conditions,p.acceptance_relation
           FROM sub_goals g
           LEFT JOIN sub_goal_production_plans p ON p.goal_id=g.id
           WHERE g.id=? AND g.sub_plan_version_id=? AND g.stage_id=?""",
        (goal_id, version_id, stage_id)
    ).fetchone()
    if row is None:
        raise SubGoalError("生产任务绑定的子目标与子计划版本或策略阶段不一致")
    if row["plan_enabled"] is None or not bool(row["plan_enabled"]):
        raise SubGoalError(f"子目标「{row['title']}」缺少有效生产计划")
    if row["status"] not in {"ready", "in_progress"}:
        raise SubGoalError(f"子目标「{row['title']}」当前状态为 {row['status']}，不允许生产")
    unmet = conn.execute(
        """SELECT g.title FROM sub_goal_dependencies d
           JOIN sub_goals g ON g.id=d.depends_on_goal_id
           WHERE d.goal_id=? AND g.status!='completed'""", (goal_id,)
    ).fetchall()
    if unmet:
        raise SubGoalError(
            f"子目标「{row['title']}」前置依赖尚未完成：" + "、".join(item["title"] for item in unmet)
        )
    return dict(row)
