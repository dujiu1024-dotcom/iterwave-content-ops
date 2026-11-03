"""Persistence helpers for immutable visual versions and page-scoped jobs.

The caller owns the SQLite transaction and commit boundary.
"""
from __future__ import annotations

import json
import uuid
import hashlib
from typing import Any


def compute_content_hash(title: str, body: str) -> str:
    """Stable hash for the textual content payload; visual styling is version metadata."""
    normalized_title = str(title or "")
    normalized_body = str(body or "")
    return hashlib.sha256((normalized_title + "\0" + normalized_body).encode("utf-8")).hexdigest()


class VisualJobStateError(RuntimeError):
    """Raised when a visual job cannot make the requested state transition."""


def create_visual_version(
    conn, *, content_id: str, content_hash: str, renderer: str,
    style_key: str, design: dict[str, Any], created_by: str | None = None,
) -> int:
    if renderer not in {"chromium_v2", "legacy_v1"}:
        raise ValueError("unsupported visual renderer")
    if not isinstance(design, dict):
        raise TypeError("design must be a JSON object")
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM visual_design_versions WHERE content_id = ?",
        (content_id,),
    ).fetchone()
    version = int(row[0])
    cursor = conn.execute(
        "INSERT INTO visual_design_versions "
        "(content_id, version, content_hash, renderer, style_key, design_json, status, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
        (content_id, version, content_hash, renderer, style_key,
         json.dumps(design, ensure_ascii=False, sort_keys=True), created_by),
    )
    return int(cursor.lastrowid)


def create_visual_page_job(
    conn, *, content_id: str, page_index: int, job_type: str,
    visual_version_id: int | None = None, input_data: dict[str, Any] | None = None,
) -> str:
    if page_index < 0:
        raise ValueError("page_index must be non-negative")
    if job_type not in {"generate", "revise", "rerender", "review"}:
        raise ValueError("unsupported visual job type")
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO visual_jobs "
        "(id, content_id, visual_version_id, page_index, job_type, input_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, content_id, visual_version_id, page_index, job_type,
         json.dumps(input_data or {}, ensure_ascii=False, sort_keys=True)),
    )
    return job_id


def transition_visual_job(
    conn, *, job_id: str, expected_status: str, new_status: str,
    output_data: dict[str, Any] | None = None, error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    allowed = {
        "queued": {"running", "cancelled"},
        "running": {"succeeded", "failed", "cancelled"},
        "failed": {"queued"},
    }
    if new_status not in allowed.get(expected_status, set()):
        raise VisualJobStateError(f"invalid visual job transition: {expected_status} -> {new_status}")
    if new_status == "running":
        cursor = conn.execute(
            "UPDATE visual_jobs SET status='running', attempt=attempt+1, started_at=CURRENT_TIMESTAMP, "
            "error_code=NULL, error_message=NULL WHERE id=? AND status=?",
            (job_id, expected_status),
        )
    else:
        cursor = conn.execute(
            "UPDATE visual_jobs SET status=?, output_json=?, error_code=?, error_message=?, "
            "completed_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
            (new_status, json.dumps(output_data or {}, ensure_ascii=False, sort_keys=True),
             error_code, (error_message or "")[:1000] or None, job_id, expected_status),
        )
    if cursor.rowcount != 1:
        raise VisualJobStateError("visual job missing or state changed concurrently")


def get_visual_version_history(conn, content_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, version, content_hash, renderer, style_key, design_json, status, created_at "
        "FROM visual_design_versions WHERE content_id=? ORDER BY version DESC",
        (content_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["design"] = json.loads(item.pop("design_json"))
        except (TypeError, ValueError):
            item["design"] = {}
        result.append(item)
    return result
