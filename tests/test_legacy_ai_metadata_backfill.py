from __future__ import annotations

import json

import pytest

from src.core.db import Database
from src.core.plan_version_store import PlanVersionError, PlanVersionStore


def _legacy_ai_note(metadata: dict) -> str:
    return "AI 母计划草稿：" + json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"),
    )


def _plan_snapshot() -> dict:
    return {
        "topic_count_per_platform": 5,
        "enabled_platforms": ["xhs"],
        "dimension_allocation": {"AI教育": 5},
    }


def test_initialize_backfills_legacy_ai_change_note_into_signed_metadata(cfg, conn):
    metadata = {
        "generation_source": "confirmed_business_diagnosis",
        "diagnosis_report_id": 12,
        "diagnosis_version_no": 3,
        "source_fingerprint": "source-fingerprint",
        "strategy_fingerprint": "strategy-fingerprint",
        "summary": "历史 AI 摘要",
        "assumptions": ["受众以新手为主"],
        "risks": ["素材储备不足"],
        "pending_confirmations": ["确认预算"],
        "resource_basis": {"weekly_capacity": 5},
    }
    legacy_note = _legacy_ai_note(metadata)
    draft = PlanVersionStore(conn).create_master_draft(
        _plan_snapshot(), author="ai", source="system",
        change_note="placeholder legacy note",
    )
    conn.execute(
        "UPDATE master_plan_versions SET change_note=? WHERE id=?",
        (legacy_note, draft["id"]),
    )
    conn.commit()

    # The startup path must backfill even when no SQL migration is pending.
    Database(cfg).initialize()
    Database(cfg).initialize()

    migrated = conn.execute(
        "SELECT change_note,ai_metadata_json,effective_metadata_json,metadata_signature,"
        "metadata_version,metadata_updated_by FROM master_plan_versions WHERE id=?",
        (draft["id"],),
    ).fetchone()
    assert migrated["change_note"] == legacy_note
    assert migrated["metadata_signature"]
    assert migrated["metadata_version"] == 1
    assert migrated["metadata_updated_by"] == "ai"
    assert json.loads(migrated["ai_metadata_json"]) == {
        "schema_version": 1, "original": metadata, "effective": metadata,
    }
    assert json.loads(migrated["effective_metadata_json"]) == metadata
    assert conn.execute(
        "SELECT COUNT(*) FROM master_plan_metadata_revisions WHERE master_plan_version_id=?",
        (draft["id"],),
    ).fetchone()[0] == 1

    # Migrated historical AI provenance is now verifiable and reviewable.
    activated = PlanVersionStore(conn).activate_master_version(
        draft["id"], reviewer="reviewer",
    )
    assert activated["status"] == "effective"


def test_initialize_preserves_invalid_legacy_ai_note_and_keeps_it_fail_closed(cfg, conn):
    draft = PlanVersionStore(conn).create_master_draft(
        _plan_snapshot(), author="ai", source="system",
        change_note="placeholder legacy note",
    )
    invalid_note = "AI 母计划草稿：{not-json"
    conn.execute(
        "UPDATE master_plan_versions SET change_note=? WHERE id=?",
        (invalid_note, draft["id"]),
    )
    conn.commit()

    Database(cfg).initialize()

    row = conn.execute(
        "SELECT change_note,ai_metadata_json,effective_metadata_json,metadata_signature "
        "FROM master_plan_versions WHERE id=?",
        (draft["id"],),
    ).fetchone()
    assert row["change_note"] == invalid_note
    assert row["ai_metadata_json"] is None
    assert row["effective_metadata_json"] is None
    assert row["metadata_signature"] is None
    with pytest.raises(PlanVersionError, match="历史 AI 草稿元数据尚未纳入完整签名"):
        PlanVersionStore(conn).activate_master_version(draft["id"], reviewer="reviewer")
