"""Append-only audit for reference assets actually accepted by an image provider."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class AssetConsumptionAuditError(RuntimeError):
    """Raised when a provider-consumption event cannot be tied to an approved task snapshot."""


def record_visual_asset_consumption(
    *,
    db_path: str | Path,
    production_task_id: str,
    asset: dict,
    page_role: str,
    page_index: int,
    provider: str,
    model: str,
    result_sha256: str,
    prompt_fingerprint: str,
) -> str:
    """Append one idempotent consumption event after a provider returns a valid image."""
    if not production_task_id:
        raise AssetConsumptionAuditError("production_task_id_required")
    if not isinstance(asset, dict):
        raise AssetConsumptionAuditError("asset_descriptor_invalid")
    asset_id = int(asset.get("asset_id") or 0)
    version_no = int(asset.get("version_no") or 0)
    source_sha = str(asset.get("sha256") or "").strip().lower()
    result_sha = str(result_sha256 or "").strip().lower()
    if asset_id <= 0 or version_no <= 0:
        raise AssetConsumptionAuditError("asset_identity_invalid")
    if len(source_sha) != 64 or any(c not in "0123456789abcdef" for c in source_sha):
        raise AssetConsumptionAuditError("source_sha256_invalid")
    if len(result_sha) != 64 or any(c not in "0123456789abcdef" for c in result_sha):
        raise AssetConsumptionAuditError("result_sha256_invalid")
    role = str(page_role or "").strip().lower()
    if role not in {"cover", "body", "ending"} or int(page_index) < 0:
        raise AssetConsumptionAuditError("page_role_or_index_invalid")

    conn = sqlite3.connect(str(Path(db_path)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        task = conn.execute(
            "SELECT input_snapshot_json FROM production_tasks WHERE id=?",
            (production_task_id,),
        ).fetchone()
        if task is None:
            raise AssetConsumptionAuditError("production_task_not_found")
        try:
            snapshot = json.loads(task["input_snapshot_json"] or "{}")
        except (TypeError, ValueError) as exc:
            raise AssetConsumptionAuditError("production_task_snapshot_invalid") from exc
        task_input = snapshot.get("task_input") if isinstance(snapshot, dict) else None
        if not isinstance(task_input, dict):
            raise AssetConsumptionAuditError("production_task_input_missing")

        candidates = task_input.get("original_assets")
        candidate = next((
            item for item in candidates if isinstance(item, dict)
            and int(item.get("asset_id") or 0) == asset_id
            and int(item.get("version_no") or 0) == version_no
        ), None) if isinstance(candidates, list) else None
        if candidate is None or str(candidate.get("sha256") or "").lower() != source_sha:
            raise AssetConsumptionAuditError("asset_not_in_immutable_task_snapshot")

        selected_by_role = (
            (task_input.get("visual_asset_selection") or {}).get("selected_by_role")
            if isinstance(task_input.get("visual_asset_selection"), dict) else None
        )
        selected = selected_by_role.get(role) if isinstance(selected_by_role, dict) else None
        if not isinstance(selected, list) or not any(
            isinstance(item, dict)
            and int(item.get("asset_id") or 0) == asset_id
            and int(item.get("version_no") or 0) == version_no
            and str(item.get("sha256") or "").lower() == source_sha
            for item in selected
        ):
            raise AssetConsumptionAuditError("asset_not_selected_for_page_role")

        allowed = (
            candidate.get("review_status") == "confirmed"
            and candidate.get("commercial_use_status") == "allowed"
            and candidate.get("privacy_status") in {"clear", "not_applicable"}
            and candidate.get("readability_status") in {"readable", "not_applicable"}
            and candidate.get("completeness_status") == "complete"
            and candidate.get("duplicate_status") == "unique"
        )
        if not allowed:
            raise AssetConsumptionAuditError("asset_snapshot_permission_or_quality_denied")

        event_material = {
            "task_id": production_task_id, "asset_id": asset_id,
            "version_no": version_no, "page_role": role,
            "page_index": int(page_index), "source_sha256": source_sha,
            "result_sha256": result_sha, "prompt_fingerprint": str(prompt_fingerprint or ""),
        }
        event_id = hashlib.sha256(
            json.dumps(event_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        usage_role = f"consumed:{role}:{int(page_index)}:{event_id[:16]}"
        quality_snapshot = json.dumps({
            "readability_status": candidate.get("readability_status"),
            "completeness_status": candidate.get("completeness_status"),
            "duplicate_status": candidate.get("duplicate_status"),
            "commercial_use_status": candidate.get("commercial_use_status"),
            "asset_version_no": version_no,
        }, sort_keys=True, separators=(",", ":"))
        consumed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            """INSERT OR IGNORE INTO production_task_asset_usages(
                production_task_id,original_asset_id,asset_version_no,usage_role,
                permission_snapshot,privacy_snapshot,asset_quality_snapshot,
                provider,model,source_sha256,result_sha256,generation_event_id,consumed_at
            ) VALUES(?,?,?,?,'allowed',?,?,?,?,?,?,?,?)""",
            (
                production_task_id, asset_id, version_no, usage_role,
                candidate.get("privacy_status"), quality_snapshot,
                str(provider or "")[:120], str(model or "")[:160],
                source_sha, result_sha, event_id, consumed_at,
            ),
        )
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
