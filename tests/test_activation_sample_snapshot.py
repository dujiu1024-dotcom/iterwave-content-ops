from __future__ import annotations

import json
import sqlite3

import pytest

from src.core.activation_sample_snapshot import (
    ActivationSampleSnapshotError,
    activation_sample_content_hash,
    build_activation_sample_snapshot,
    verify_activation_sample_snapshot,
)


def sample(**overrides):
    value = {
        "id": "sample-1", "tenant_id": "tenant-a", "platform": "xhs",
        "content_version": 1, "title": "不可变标题", "body": "不可变正文",
        "hashtags_json": json.dumps(["实践", "效率"], ensure_ascii=False),
    }
    value.update(overrides)
    return value


def test_activation_snapshot_is_canonical_and_preserves_exact_copy():
    first = build_activation_sample_snapshot(sample())
    second = build_activation_sample_snapshot(sample(hashtags_json='["实践", "效率"]'))
    assert first == second
    assert first["title"] == "不可变标题"
    assert first["body"] == "不可变正文"
    assert first["tags"] == ["实践", "效率"]
    assert len(first["content_hash"]) == 64
    assert activation_sample_content_hash(sample()) == first["content_hash"]


def test_activation_snapshot_rejects_identity_version_and_content_changes():
    frozen = build_activation_sample_snapshot(sample())
    for changed in (
        sample(tenant_id="tenant-b"), sample(platform="douyin"),
        sample(content_version=2), sample(body="被修改的正文"),
    ):
        with pytest.raises(ActivationSampleSnapshotError):
            verify_activation_sample_snapshot(changed, frozen)


def test_activation_snapshot_accepts_sqlite_row_mapping():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sample (id TEXT, tenant_id TEXT, platform TEXT, content_version INTEGER, "
        "title TEXT, body TEXT, hashtags_json TEXT)"
    )
    conn.execute(
        "INSERT INTO sample VALUES ('sample-db','tenant-db','xhs',1,'标题','正文','[]')"
    )
    row = conn.execute("SELECT * FROM sample").fetchone()
    frozen = build_activation_sample_snapshot(row)
    assert frozen["sample_content_id"] == "sample-db"
    assert frozen["content_version"] == 1
    conn.close()


def test_activation_snapshot_rejects_malformed_copy_fields():
    with pytest.raises(ActivationSampleSnapshotError):
        build_activation_sample_snapshot(sample(title="  "))
    with pytest.raises(ActivationSampleSnapshotError):
        build_activation_sample_snapshot(sample(hashtags_json="not-json"))
    with pytest.raises(ActivationSampleSnapshotError):
        build_activation_sample_snapshot(sample(content_version=True))


def test_activation_sample_content_version_advances_only_for_copy_edits(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sample_contents)")}
    assert "content_version" in columns
    conn.execute(
        "INSERT INTO sample_runs (id,tenant_id,platform,account_description,status,idempotency_key,created_at) "
        "VALUES ('version-run','default','xhs','分享产品经理实践方法','ready','version-key',CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO sample_contents "
        "(id,sample_run_id,tenant_id,platform,angle,title,body,hashtags_json,quality_status,created_at,updated_at) "
        "VALUES ('version-sample','version-run','default','xhs','经验','标题','正文','[]','pass',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    )
    conn.commit()
    assert conn.execute("SELECT content_version FROM sample_contents WHERE id='version-sample'").fetchone()[0] == 1
    conn.execute("UPDATE sample_contents SET promoted_at=CURRENT_TIMESTAMP WHERE id='version-sample'")
    conn.commit()
    assert conn.execute("SELECT content_version FROM sample_contents WHERE id='version-sample'").fetchone()[0] == 1
    conn.execute("UPDATE sample_contents SET body='编辑后的正文' WHERE id='version-sample'")
    conn.commit()
    assert conn.execute("SELECT content_version FROM sample_contents WHERE id='version-sample'").fetchone()[0] == 2
