import json

from src.core import skill_execution, tenant_skills


def test_execution_is_fail_closed_and_auditable(conn):
    manifest = {"skill_id":"deny_skill","name":"Deny","version":"1.0.0",
                "instructions":"untrusted prompt", "permissions":[]}
    skill = tenant_skills.create_skill(conn, manifest)
    result = skill_execution.request_execution(conn, skill, {"text":"do not run"})
    assert result["status"] == "rejected"
    assert result["reason_code"] == "sandbox_unavailable"
    assert result["execution_enabled"] is False
    row = conn.execute("SELECT status,reason_code,input_sha256 FROM tenant_skill_executions WHERE execution_id=?",
                       (result["execution_id"],)).fetchone()
    assert row["status"] == "rejected"
    assert row["reason_code"] == "sandbox_unavailable"
    assert len(row["input_sha256"]) == 64
    audit = tenant_skills.list_audit(conn, "deny_skill")
    assert any(item["action"] == "execution_rejected" for item in audit)


def test_execution_rejects_oversized_and_non_json_payload(conn):
    skill = tenant_skills.create_skill(conn, {"skill_id":"large_skill","name":"Large","version":"1.0.0"})
    large = skill_execution.request_execution(conn, skill, {"data":"x" * skill_execution.MAX_INPUT_BYTES})
    assert large["reason_code"] == "input_too_large"
    invalid = skill_execution.request_execution(conn, skill, {"data": float("nan")})
    assert invalid["reason_code"] == "invalid_json"
    assert conn.execute("SELECT COUNT(*) FROM tenant_skill_executions").fetchone()[0] == 2
