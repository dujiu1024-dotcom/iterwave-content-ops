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
