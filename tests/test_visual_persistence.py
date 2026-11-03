import sqlite3
from pathlib import Path

import pytest

from src.core.visual_persistence import (
    VisualJobStateError,
    compute_content_hash,
    create_visual_page_job,
    create_visual_version,
    get_visual_version_history,
    transition_visual_job,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE contents (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO contents(id) VALUES ('content-1')")
    migration = Path(__file__).parents[1] / "migrations" / "117_visual_design_versions_jobs.sql"
    connection.executescript(migration.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def test_visual_versions_are_immutable_and_increment_per_content(conn):
    first = create_visual_version(
        conn, content_id="content-1", content_hash="hash-a",
        renderer="chromium_v2", style_key="editorial_magazine",
        design={"pages": [{"title": "封面"}]},
    )
    second = create_visual_version(
        conn, content_id="content-1", content_hash="hash-b",
        renderer="chromium_v2", style_key="editorial_magazine",
        design={"pages": [{"title": "新版封面"}]},
    )
    history = get_visual_version_history(conn, "content-1")
    assert [item["version"] for item in history] == [2, 1]
    assert history[0]["id"] == second
    assert history[1]["id"] == first
    assert history[1]["design"]["pages"][0]["title"] == "封面"


def test_visual_page_job_state_transitions_are_guarded(conn):
    version_id = create_visual_version(
        conn, content_id="content-1", content_hash="hash-a",
        renderer="chromium_v2", style_key="knowledge_infographic", design={},
    )
    job_id = create_visual_page_job(
        conn, content_id="content-1", visual_version_id=version_id,
        page_index=2, job_type="revise", input_data={"instruction": "改进留白"},
    )
    transition_visual_job(conn, job_id=job_id, expected_status="queued", new_status="running")
    row = conn.execute("SELECT status, attempt FROM visual_jobs WHERE id=?", (job_id,)).fetchone()
    assert tuple(row) == ("running", 1)
    transition_visual_job(
        conn, job_id=job_id, expected_status="running", new_status="succeeded",
        output_data={"asset_path": "page-2.png"},
    )
    row = conn.execute("SELECT status, output_json FROM visual_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "succeeded"
    assert '"asset_path": "page-2.png"' in row["output_json"]


def test_visual_job_rejects_invalid_or_stale_transitions(conn):
    job_id = create_visual_page_job(
        conn, content_id="content-1", page_index=0, job_type="generate",
    )
    with pytest.raises(VisualJobStateError):
        transition_visual_job(
            conn, job_id=job_id, expected_status="queued", new_status="succeeded",
        )
    transition_visual_job(conn, job_id=job_id, expected_status="queued", new_status="running")
    with pytest.raises(VisualJobStateError):
        transition_visual_job(
            conn, job_id=job_id, expected_status="queued", new_status="running",
        )


def test_visual_version_rejects_unknown_renderer(conn):
    with pytest.raises(ValueError):
        create_visual_version(
            conn, content_id="content-1", content_hash="hash-a",
            renderer="unknown", style_key="x", design={},
        )


def test_content_hash_uses_one_canonical_text_delimiter():
    assert compute_content_hash("标题", "正文") == compute_content_hash("标题", "正文")
    assert compute_content_hash("标题", "正文") != compute_content_hash("标题正文", "")
    assert compute_content_hash("标题", "正文") != compute_content_hash("标题", "正文2")
