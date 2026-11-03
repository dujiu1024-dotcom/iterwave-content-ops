from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.tenant_settings import build_tenant_settings_router
from src.core.config_store import ConfigNotAllowedError, ConfigPatchError


def _client(monkeypatch):
    import src.core.ops_targets_repository as ops_repo
    import src.core.industry_presets_repository as preset_repo

    context = {
        "conn": object(),
        "cfg": SimpleNamespace(
            ops_targets=None,
            ultimate_goal=SimpleNamespace(redline_keywords=["base"]),
        ),
    }
    calls = {"patch": None}
    preset = {
        "key": "sample",
        "name": "示例行业",
        "positioning": "帮助用户解决实际问题",
        "persona_keywords": ["方法"],
        "forbidden_directions": ["夸大承诺"],
        "extra_redlines": ["隐私泄露"],
        "dimensions": ["教程"],
        "platform_notes": {"xhs": "图文"},
        "is_active": True,
    }

    monkeypatch.setattr(ops_repo, "read_targets", lambda _conn: {"xhs": {"follows": ["a"]}})
    monkeypatch.setattr(ops_repo, "replace_targets", lambda _conn, payload, cfg=None: payload)
    monkeypatch.setattr(ops_repo, "load_ops_targets", lambda _conn: {"xhs": "loaded"})
    monkeypatch.setattr(preset_repo, "list_presets", lambda _conn: [preset])
    monkeypatch.setattr(preset_repo, "upsert_custom_preset", lambda _conn, item, cfg=None: item)
    monkeypatch.setattr(preset_repo, "delete_custom_preset", lambda _conn, key, cfg=None: key == "sample")
    monkeypatch.setattr(preset_repo, "restore_custom_preset", lambda _conn, key, cfg=None: preset if key == "sample" else None)
    monkeypatch.setattr(preset_repo, "get_preset", lambda _conn, key: preset if key == "sample" else None)
    monkeypatch.setattr(preset_repo, "list_preset_versions", lambda _conn, key: [{"version": 1}])

    def apply_patch(cfg, conn, updates):
        calls["patch"] = updates

    app = FastAPI()
    app.include_router(build_tenant_settings_router(
        lambda: context, apply_patch, ConfigNotAllowedError, ConfigPatchError,
    ))
    return TestClient(app), context, calls, preset


def test_ops_targets_routes_preserve_repository_payload_and_update_runtime_config(monkeypatch):
    client, context, _calls, _preset = _client(monkeypatch)
    read = client.get("/api/ops-targets")
    assert read.status_code == 200
    assert read.json()["items"]["xhs"]["follows"] == ["a"]

    payload = {"xhs": {"follows": ["new-target"], "active_comments": []}}
    saved = client.put("/api/ops-targets", json=payload)
    assert saved.status_code == 200
    assert saved.json()["items"] == payload
    assert context["cfg"].ops_targets == {"xhs": "loaded"}


def test_preset_routes_preserve_crud_versions_and_apply_contract(monkeypatch):
    client, _context, calls, _preset = _client(monkeypatch)
    assert client.get("/api/presets").json()["items"][0]["key"] == "sample"
    saved = client.put("/api/presets/custom", json={"name": "自定义"} )
    assert saved.status_code == 200
    assert saved.json()["key"] == "custom"
    assert client.get("/api/presets/sample/versions").json()["items"] == [{"version": 1}]
    assert client.delete("/api/presets/sample").json() == {"key": "sample", "deleted": True}
    assert client.post("/api/presets/sample/restore").json()["key"] == "sample"

    applied = client.post("/api/presets/sample/apply")
    assert applied.status_code == 200
    assert applied.json()["key"] == "sample"
    assert calls["patch"]["ultimate_goal.redline_keywords"] == ["base", "隐私泄露"]


def test_preset_routes_keep_not_found_and_archived_conflicts(monkeypatch):
    client, _context, _calls, preset = _client(monkeypatch)
    assert client.delete("/api/presets/missing").status_code == 404
    assert client.post("/api/presets/missing/restore").status_code == 404
    preset["is_active"] = False
    response = client.post("/api/presets/sample/apply")
    assert response.status_code == 409
