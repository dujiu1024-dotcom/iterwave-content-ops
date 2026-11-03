import json
import pytest

from src.core import tenant_skills


def manifest(version="1.0.0", **overrides):
    value = {"skill_id":"sample_skill","name":"Sample","version":version,
             "description":"safe metadata","instructions":"Return structured data only",
             "permissions":["read:own_inputs"],"inputs":{"type":"object"},"outputs":{"type":"object"}}
    value.update(overrides)
    return value


def test_manifest_validation_rejects_invalid_id_version_and_permissions():
    result = tenant_skills.validate_manifest(manifest(skill_id="../escape", version="latest",
                                                        permissions=["network:any"]))
    assert result["valid"] is False
    assert {e["field"] for e in result["errors"]} >= {"skill_id","version","permissions"}


def test_registry_crud_versions_audit_and_persistence(conn):
    created = tenant_skills.create_skill(conn, manifest())
    assert created["skill_id"] == "sample_skill"
    assert created["enabled"] == 0
    assert created["execution_enabled"] is False
    assert created["validation"]["valid"] is True
    assert tenant_skills.list_versions(conn, "sample_skill")[0]["version"] == "1.0.0"
    assert tenant_skills.set_enabled(conn, "sample_skill", True)["enabled"] == 1
    updated = tenant_skills.update_skill(conn, "sample_skill", manifest("1.1.0", description="revised"))
    assert updated["version"] == "1.1.0"
    assert updated["validation"]["valid"] is True
    assert [v["version"] for v in tenant_skills.list_versions(conn, "sample_skill")] == ["1.1.0","1.0.0"]
    assert tenant_skills.list_audit(conn, "sample_skill")
    assert tenant_skills.set_enabled(conn, "sample_skill", False)["enabled"] == 0
    assert tenant_skills.delete_skill(conn, "sample_skill") is True
    assert tenant_skills.get_skill(conn, "sample_skill") is None


def test_duplicate_registration_and_same_version_update_rejected(conn):
    tenant_skills.create_skill(conn, manifest())
    with pytest.raises(FileExistsError):
        tenant_skills.create_skill(conn, manifest())
    with pytest.raises(ValueError):
        tenant_skills.update_skill(conn, "sample_skill", manifest())
    with pytest.raises(ValueError):
        tenant_skills.update_skill(conn, "sample_skill", manifest("0.9.0"))


def test_delete_rejected_while_referenced(conn):
    tenant_skills.create_skill(conn, manifest())
    conn.execute("INSERT INTO tenant_skill_references(skill_id,reference_type,reference_id,created_at) VALUES(?,?,?,?)",
                 ("sample_skill","workflow","wf-1","2026-01-01T00:00:00Z"))
    with pytest.raises(RuntimeError):
        tenant_skills.delete_skill(conn, "sample_skill")


def test_custom_skill_never_becomes_executable(conn):
    item = tenant_skills.create_skill(conn, manifest())
    assert item["execution_enabled"] is False
    assert item["execution_state"] == "blocked_no_sandbox"
