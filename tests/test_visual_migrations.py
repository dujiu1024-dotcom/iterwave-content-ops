"""117/118 must apply on an existing database that already ran every earlier migration."""
import json
import shutil
from pathlib import Path

import src.core.db as db_module
from src.core.db import Database

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
VISUAL_MIGRATIONS = {"117_visual_design_versions_jobs.sql", "118_visual_brand_profiles.sql"}


def test_visual_migrations_apply_on_populated_116_database(cfg, tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy_migrations"
    legacy_dir.mkdir()
    for path in REPO_MIGRATIONS.glob("*.sql"):
        if path.name not in VISUAL_MIGRATIONS:
            shutil.copy(path, legacy_dir / path.name)
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", legacy_dir)
    database = Database(cfg)
    database.initialize()
    conn = database.connect()
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    assert 116 in applied and 117 not in applied and 118 not in applied
    conn.execute(
        "INSERT INTO spiral_rounds (round_number, status, topic_ids, content_variants) VALUES (1, 'completed', '[]', '[]')"
    )
    round_id = conn.execute("SELECT id FROM spiral_rounds").fetchone()[0]
    conn.execute(
        "INSERT INTO contents (id, round_id, platform, title, body, visual_status) "
        "VALUES ('c-1', ?, 'xhs', '标题', '正文', 'degraded')", (round_id,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", REPO_MIGRATIONS)
    Database(cfg).initialize()
    conn = Database(cfg).connect()
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    assert {116, 117, 118} <= applied
    assert conn.execute("SELECT title FROM contents WHERE id='c-1'").fetchone()[0] == "标题"
    conn.execute(
        "INSERT INTO visual_design_versions (content_id, version, content_hash, renderer, style_key, design_json) "
        "VALUES ('c-1', 1, 'h', 'chromium_v2', 'knowledge_infographic', ?)", (json.dumps({}),),
    )
    conn.execute(
        "INSERT INTO visual_jobs (id, content_id, page_index, job_type) VALUES ('j1', 'c-1', 0, 'generate')"
    )
    conn.execute("INSERT INTO visual_brand_profiles (tenant_id, profile_json) VALUES ('default', '{}')")
    conn.commit()
    # contents.visual_status CHECK is unchanged: only degraded/media_asset/generated.
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE contents SET visual_status='chromium' WHERE id='c-1'")
    conn.close()
    # Re-running is a no-op.
    Database(cfg).initialize()
