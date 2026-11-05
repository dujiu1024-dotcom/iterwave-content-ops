from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import subgoals


def test_subgoal_acceptance_uses_authenticated_reviewer(monkeypatch):
    captured = {}

    monkeypatch.setattr(subgoals.auth, "audit_actor", lambda request: "session:0123456789abcdef0123")

    class FakeStore:
        def __init__(self, conn):
            pass

        def accept(self, sub_plan_id, goal_id, payload, reviewer="user"):
            captured["reviewer"] = reviewer
            return {"id": goal_id, "status": "completed", "reviewed_by": reviewer}

    monkeypatch.setattr(subgoals, "SubGoalStore", FakeStore)

    app = FastAPI()
    subgoals.register_subgoal_routes(app, lambda: {"conn": object()})

    response = TestClient(app).post(
        "/api/subplans/sub-1/goals/goal-1/acceptance",
        json={"decision": "passed", "evidence": {"proof": "ok"}},
    )

    assert response.status_code == 200
    assert captured["reviewer"] == "session:0123456789abcdef0123"
    assert response.json()["goal"]["reviewed_by"] == "session:0123456789abcdef0123"


def test_subgoal_mutations_use_authenticated_actor(monkeypatch):
    captured = []
    actor = "session:0123456789abcdef0123"
    monkeypatch.setattr(subgoals.auth, "audit_actor", lambda request: actor)

    class FakeStore:
        def __init__(self, conn):
            pass

        def create_goal(self, sub_plan_id, version_id, payload, *, actor="system:unknown"):
            captured.append(("create", actor))
            return {"id": "goal-1", "created_by": actor, "updated_by": actor}

        def update_goal(self, sub_plan_id, goal_id, payload, *, actor="system:unknown"):
            captured.append(("update", actor))
            return {"id": goal_id, "updated_by": actor}

        def save_production_plan(self, sub_plan_id, goal_id, payload, *, actor="system:unknown"):
            captured.append(("production_plan", actor))
            return {"goal_id": goal_id, "created_by": actor, "updated_by": actor}

    monkeypatch.setattr(subgoals, "SubGoalStore", FakeStore)
    app = FastAPI()
    subgoals.register_subgoal_routes(app, lambda: {"conn": object()})
    client = TestClient(app)

    created = client.post("/api/subplans/sub-1/goals", json={"version_id": 3})
    updated = client.patch("/api/subplans/sub-1/goals/goal-1", json={"title": "changed"})
    planned = client.put(
        "/api/subplans/sub-1/goals/goal-1/production-plan",
        json={"content_direction": "x"},
    )

    assert created.status_code == 201
    assert updated.status_code == planned.status_code == 200
    assert captured == [
        ("create", actor), ("update", actor), ("production_plan", actor)
    ]
