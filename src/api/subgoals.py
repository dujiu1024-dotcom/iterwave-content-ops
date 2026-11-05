"""API routes for version-bound subgoal trees and goal production plans."""
from __future__ import annotations

import sqlite3

from fastapi import FastAPI, HTTPException

from src.core import auth
from src.core.subgoal_store import SubGoalError, SubGoalStore


def register_subgoal_routes(app: FastAPI, get_runtime) -> None:
    def fail(exc: Exception):
        message = str(exc)
        status = 404 if "不存在" in message else 409
        raise HTTPException(status, message) from exc

    @app.get("/api/subplans/{sub_plan_id}/goals")
    def list_subplan_goals(sub_plan_id: str, version_id: int):
        try:
            return {"items": SubGoalStore(get_runtime()["conn"]).list_goals(sub_plan_id, version_id)}
        except SubGoalError as exc:
            fail(exc)

    @app.post("/api/subplans/{sub_plan_id}/goals", status_code=201)
    def create_subplan_goal(sub_plan_id: str, req: dict):
        try:
            goal = SubGoalStore(get_runtime()["conn"]).create_goal(
                sub_plan_id, int(req.get("version_id") or 0), req
            )
            return {"goal": goal}
        except (SubGoalError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            fail(SubGoalError(str(exc)))

    @app.patch("/api/subplans/{sub_plan_id}/goals/{goal_id}")
    def update_subplan_goal(sub_plan_id: str, goal_id: str, req: dict):
        try:
            return {"goal": SubGoalStore(get_runtime()["conn"]).update_goal(sub_plan_id, goal_id, req)}
        except (SubGoalError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            fail(SubGoalError(str(exc)))

    @app.put("/api/subplans/{sub_plan_id}/goals/{goal_id}/production-plan")
    def save_subgoal_production_plan(sub_plan_id: str, goal_id: str, req: dict):
        try:
            return {"production_plan": SubGoalStore(get_runtime()["conn"]).save_production_plan(sub_plan_id, goal_id, req)}
        except (SubGoalError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            fail(SubGoalError(str(exc)))

    @app.post("/api/subplans/{sub_plan_id}/goals/{goal_id}/acceptance")
    def accept_subplan_goal(sub_plan_id: str, goal_id: str, req: dict):
        try:
            return {"goal": SubGoalStore(get_runtime()["conn"]).accept(sub_plan_id, goal_id, req)}
        except (SubGoalError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            fail(SubGoalError(str(exc)))
