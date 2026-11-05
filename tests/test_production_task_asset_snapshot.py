"""Regression coverage for immutable production-task asset classification snapshots."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_privacy_enum_and_quality_json_snapshots_have_separate_storage():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE production_task_asset_usages (
            id INTEGER PRIMARY KEY,
            production_task_id TEXT NOT NULL,
            original_asset_id INTEGER NOT NULL,
            asset_version_no INTEGER NOT NULL,
            permission_snapshot TEXT NOT NULL
                CHECK (permission_snapshot IN ('allowed', 'restricted'))
        )
        """
    )
    conn.executescript(
        (ROOT / "migrations" / "156_production_task_asset_privacy_snapshot.sql")
        .read_text(encoding="utf-8")
    )
    conn.executescript(
        (ROOT / "migrations" / "161_production_task_asset_quality_snapshot.sql")
        .read_text(encoding="utf-8")
    )

    quality = {
        "readability_status": "readable",
        "completeness_status": "complete",
        "duplicate_status": "unique",
        "commercial_use_status": "allowed",
        "asset_version_no": 3,
    }
    conn.execute(
        """
        INSERT INTO production_task_asset_usages(
            production_task_id, original_asset_id, asset_version_no,
            permission_snapshot, privacy_snapshot, asset_quality_snapshot
        ) VALUES ('task-1', 9, 3, 'allowed', 'clear', ?)
        """,
        (json.dumps(quality, ensure_ascii=False, sort_keys=True),),
    )
    row = conn.execute(
        """
        SELECT privacy_snapshot, asset_quality_snapshot
        FROM production_task_asset_usages WHERE production_task_id='task-1'
        """
    ).fetchone()
    assert row[0] == "clear"
    assert json.loads(row[1]) == quality

    try:
        conn.execute(
            """
            INSERT INTO production_task_asset_usages(
                production_task_id, original_asset_id, asset_version_no,
                permission_snapshot, privacy_snapshot, asset_quality_snapshot
            ) VALUES ('task-2', 9, 3, 'allowed', ?, '{}')
            """,
            ('{"privacy_status":"clear"}',),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("privacy_snapshot must remain constrained to the approved enum")
