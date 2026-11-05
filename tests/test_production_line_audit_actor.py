from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import production_lines


def test_production_line_status_uses_authenticated_actor(monkeypatch):
    captured = {}
    monkeypatch.setattr(production_lines.auth, "audit_actor", lambda request: "session:0123456789abcdef0123")

    class FakeStore:
        def __init__(self, conn):
            pass

        def set_status(self, sub_plan_id, status, *, actor, reason):
            captured["actor"] = actor
            return {"sub_plan_id": sub_plan_id, "status": status, "updated_by": actor}

    monkeypatch.setattr(production_lines, "ProductionLineStore", FakeStore)
    app = FastAPI()
    production_lines.register_production_line_routes(app, lambda: {"conn": object()})

    response = TestClient(app).patch(
        "/api/subplans/sub-1/production-line/status",
        json={"status": "paused", "reason": "maintenance"},
    )

    assert response.status_code == 200
    assert captured["actor"] == "session:0123456789abcdef0123"
    assert response.json()["production_line"]["updated_by"] == "session:0123456789abcdef0123"
