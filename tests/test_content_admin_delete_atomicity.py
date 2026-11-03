from __future__ import annotations

import json
import sqlite3

import pytest

from src.core.content_admin import DeleteBlocked, delete_contents, delete_round


def _content_with_image(cfg, conn, seed, *, round_status="created"):
    round_id = seed.round(status=round_status)
    draft_id = seed.draft(round_id, draft_status="draft_saved", fetch_status=None)
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    output_dir = cfg.resolve_path(cfg.paths.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image = output_dir / "delete-atomicity-test.png"
    image.write_bytes(b"test-image")
    conn.execute(
        "UPDATE contents SET image_paths = ? WHERE id = ?",
        (json.dumps([str(image)]), content_id),
    )
    conn.commit()
    return round_id, content_id, image


def test_delete_contents_keeps_files_when_database_commit_fails(cfg, conn, seed):
    _, content_id, image = _content_with_image(cfg, conn, seed)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE delete_commit_guard (content_id TEXT, "
        "FOREIGN KEY(content_id) REFERENCES contents(id) DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.execute(
        "CREATE TRIGGER fail_delete_contents_commit AFTER DELETE ON contents "
        "BEGIN INSERT INTO delete_commit_guard(content_id) VALUES ('missing-content'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        delete_contents(cfg, conn, [content_id])

    assert image.exists()
    assert conn.execute(
        "SELECT 1 FROM contents WHERE id = ?", (content_id,)
    ).fetchone() is not None


def test_delete_round_is_one_transaction_and_defers_file_cleanup(cfg, conn, seed):
    round_id, content_id, image = _content_with_image(cfg, conn, seed)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE round_delete_commit_guard (round_id INTEGER, "
        "FOREIGN KEY(round_id) REFERENCES spiral_rounds(id) DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.execute(
        "CREATE TRIGGER fail_delete_round_commit AFTER DELETE ON spiral_rounds "
        "BEGIN INSERT INTO round_delete_commit_guard(round_id) VALUES (-1); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        delete_round(cfg, conn, round_id)

    assert image.exists()
    assert conn.execute(
        "SELECT 1 FROM contents WHERE id = ?", (content_id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone() is not None


def test_delete_round_is_blocked_by_pending_save_verification(cfg, conn, seed):
    round_id, content_id, image = _content_with_image(cfg, conn, seed)
    draft_id = conn.execute(
        "SELECT id FROM drafts WHERE content_id = ?", (content_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO draft_save_verifications (draft_id, status, attempted_at) "
        "VALUES (?, 'pending_verify', '2026-11-02T12:00:00+08:00')",
        (draft_id,),
    )
    conn.commit()

    with pytest.raises(DeleteBlocked):
        delete_round(cfg, conn, round_id)

    assert image.exists()
    assert conn.execute(
        "SELECT 1 FROM contents WHERE id = ?", (content_id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone() is not None


def test_delete_contents_cleans_notifications_and_snapshots_then_files(cfg, conn, seed):
    """Regression: the atomic rewrite must keep main's FK cleanup of notifications/snapshots."""
    round_id, content_id, image = _content_with_image(cfg, conn, seed)
    draft_id = conn.execute(
        "SELECT id FROM drafts WHERE content_id = ?", (content_id,)
    ).fetchone()[0]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notifications)")}
    if "draft_id" not in columns:
        pytest.skip("notifications.draft_id not present in this schema")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO notifications (level, type, platform, title, draft_id, round_id) "
        "VALUES ('info', 'draft_saved', 'xhs', 'delete regression', ?, ?)",
        (draft_id, round_id),
    )
    conn.commit()

    result = delete_round(cfg, conn, round_id)

    assert result["contents_deleted"] == 1
    assert result["files_removed"] == 1
    assert not image.exists()
    assert conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE draft_id = ?", (draft_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT 1 FROM spiral_rounds WHERE id = ?", (round_id,)
    ).fetchone() is None
