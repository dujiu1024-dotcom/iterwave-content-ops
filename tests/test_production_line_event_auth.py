from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.production_lines import register_production_line_routes


def test_publish_and_close_events_reject_caller_controlled_source(monkeypatch):
    from src.core.content_production_line_store import ContentProductionLineStore

    called = []
    monkeypatch.setattr(
        ContentProductionLineStore, "transition",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    app = FastAPI()
    register_production_line_routes(app, lambda: {"conn": object(), "cfg": None})
    client = TestClient(app)

    for event_type in ("publish_verified", "closed"):
        response = client.post(
            "/api/content-production-lines/cpl-1/events",
            json={"event_type": event_type, "source": "work_item_4", "evidence": {"verification_id": "v1", "review_ref": "r1"}},
        )
        assert response.status_code == 403

    assert called == []
