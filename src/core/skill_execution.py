"""Fail-closed boundary for custom Skill execution.

No Python code, shell command, plugin, or network request is executed here.
A production executor must be a separately deployed, independently reviewed
sandbox service. This module intentionally has no host-process fallback.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

MAX_INPUT_BYTES = 1_048_576
EXECUTION_ENABLED = False
BLOCK_REASON = "sandbox_unavailable"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def request_execution(conn: sqlite3.Connection, skill: dict, payload: Any) -> dict:
    """Persist a metadata-only rejection. Never evaluates or dispatches payload."""
    execution_id = str(uuid.uuid4())
    diagnostic_id = str(uuid.uuid4())
    try:
        encoded = _canonical_json(payload)
    except (TypeError, ValueError, UnicodeError):
        encoded = b""
        reason = "invalid_json"
    else:
        reason = "input_too_large" if len(encoded) > MAX_INPUT_BYTES else BLOCK_REASON
    conn.execute(
        """INSERT INTO tenant_skill_executions
        (execution_id,skill_id,skill_version,status,reason_code,input_bytes,requested_at,diagnostic_id)
        VALUES(?,?,?,'rejected',?,?,?,?)""",
        (execution_id, skill["skill_id"], skill["version"], reason, len(encoded), _now(), diagnostic_id),
    )
    conn.execute(
        """INSERT INTO tenant_skill_audit(skill_id,action,actor,details_json,created_at)
        VALUES(?,'execution_rejected','system',?,?)""",
        (skill["skill_id"], json.dumps({"execution_id": execution_id, "diagnostic_id": diagnostic_id,
                                       "reason_code": reason}, separators=(",", ":")), _now()),
    )
    return {"execution_id": execution_id, "diagnostic_id": diagnostic_id,
            "status": "rejected", "reason_code": reason, "execution_enabled": False}
