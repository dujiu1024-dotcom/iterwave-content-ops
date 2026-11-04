from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import business_profile


def test_north_star_write_uses_authenticated_audit_actor(monkeypatch):
    captured = {}

    monkeypatch.setattr(business_profile.auth, "audit_actor", lambda request: "session:0123456789abcdef0123")

    def fake_create_version(conn, payload, *, actor):
        captured["actor"] = actor
        captured["payload"] = payload
        return {"created_by": actor}

    from src.core import north_star_repository
    monkeypatch.setattr(north_star_repository, "create_version", fake_create_version)

    app = FastAPI()
    business_profile.register_business_profile_routes(
        app, lambda: {"conn": object()}
    )

    response = TestClient(app).put(
        "/api/north-star",
        json={"change_reason": "audit test", "positioning": "example"},
    )

    assert response.status_code == 200
    assert captured["actor"] == "session:0123456789abcdef0123"
    assert response.json()["created_by"] == "session:0123456789abcdef0123"
