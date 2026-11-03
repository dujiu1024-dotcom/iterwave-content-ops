from datetime import datetime
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.notify_rules import build_notify_rules_router
from src.api.main import TransactionalAPIRoute


def test_notify_rules_router_keeps_crud_and_lazy_schema_creation():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    app = FastAPI()
    app.include_router(build_notify_rules_router(
        lambda: {"conn": conn},
        lambda: datetime(2026, 11, 2, 12, 30, 0),
        route_class=TransactionalAPIRoute,
    ))
    client = TestClient(app)

    # First read creates the optional table lazily, preserving the previous API contract.
    assert client.get("/api/notify_rules").json() == []
    created = client.post("/api/notify_rules", json={
        "scenario": "round_completed",
        "channel": "console",
        "level": "info",
        "enabled": True,
        "condition": "round status is completed",
    })
    assert created.status_code == 200
    rule_id = created.json()["id"]

    listed = client.get("/api/notify_rules")
    assert len(listed.json()) == 1
    assert listed.json()[0]["scenario"] == "round_completed"

    updated = client.patch(f"/api/notify_rules/{rule_id}", json={
        "scenario": "round_completed",
        "channel": "console",
        "level": "warning",
        "enabled": False,
    })
    assert updated.status_code == 200
    assert client.get("/api/notify_rules").json()[0]["level"] == "warning"

    deleted = client.delete(f"/api/notify_rules/{rule_id}")
    assert deleted.status_code == 200
    assert client.get("/api/notify_rules").json() == []
    conn.close()


def test_notify_rules_router_validates_channel_and_scenario():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    app = FastAPI()
    app.include_router(build_notify_rules_router(
        lambda: {"conn": conn},
        lambda: datetime(2026, 11, 2, 12, 30, 0),
    ))
    response = TestClient(app).post("/api/notify_rules", json={
        "scenario": " " * 65,
        "channel": "unknown",
        "level": "info",
    })
    assert response.status_code == 422
    conn.close()
