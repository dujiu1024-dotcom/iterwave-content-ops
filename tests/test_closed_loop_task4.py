from __future__ import annotations

import json
import sqlite3

import pytest

from src.core.closed_loop import (
    ClosedLoopError, create_gap_snapshot, create_visual_revision_audit,
    record_review_collection, verify_publication,
)


def _tables(conn):
    conn.executescript("""
    CREATE TABLE contents(id TEXT PRIMARY KEY,title TEXT,body TEXT,tags TEXT,round_id INTEGER);
    CREATE TABLE drafts(id TEXT PRIMARY KEY,content_id TEXT,user_confirmed_at TEXT,platform TEXT);
    CREATE TABLE spiral_rounds(id INTEGER PRIMARY KEY,production_line_version_id INTEGER);
    CREATE TABLE production_line_versions(id INTEGER PRIMARY KEY);
    CREATE TABLE publish_verifications(id TEXT PRIMARY KEY,draft_id TEXT UNIQUE,status TEXT,
      platform_content_id TEXT,platform_url TEXT,published_at TEXT,content_snapshot_json TEXT,
      verified_at TEXT,verified_by TEXT,created_at TEXT);
    CREATE TABLE review_tasks(id TEXT PRIMARY KEY,draft_id TEXT UNIQUE,publish_verification_id TEXT,
      production_line_version_id INTEGER,status TEXT,collection_source TEXT,collect_after TEXT,
      window_start TEXT,window_end TEXT,platform_content_id TEXT,publish_snapshot_json TEXT,
      collection_result_json TEXT DEFAULT '{}',conclusion_json TEXT DEFAULT '{}',attempts INTEGER DEFAULT 0,
      last_error TEXT,created_at TEXT,updated_at TEXT,completed_at TEXT);
    CREATE TABLE review_task_events(id INTEGER PRIMARY KEY,review_task_id TEXT,event_type TEXT,
      from_status TEXT,to_status TEXT,detail_json TEXT,created_at TEXT,actor TEXT);
    CREATE TABLE closed_loop_events(id TEXT PRIMARY KEY,event_type TEXT,aggregate_id TEXT,payload_json TEXT,status TEXT,created_at TEXT);
    CREATE TABLE business_gap_snapshots(id INTEGER PRIMARY KEY,gap_key TEXT,status TEXT,severity TEXT,
      description TEXT,evidence_json TEXT,source_review_task_id TEXT,observed_at TEXT,created_at TEXT,created_by TEXT);
    CREATE TABLE visual_revision_audits(id TEXT PRIMARY KEY,content_id TEXT,base_visual_version_id INTEGER,
      generated_visual_version_id INTEGER,scope TEXT,target_page_index INTEGER,original_reason TEXT,
      normalized_instruction TEXT,final_prompt TEXT,style_reference_json TEXT,preserved_page_hashes_json TEXT,
      status TEXT,created_at TEXT,created_by TEXT);
    """)
    conn.execute("INSERT INTO contents VALUES('c1','标题','正文','标签',1)")
    conn.execute("INSERT INTO drafts VALUES('d1','c1','2026-11-04T10:00:00','xhs')")
    conn.execute("INSERT INTO spiral_rounds VALUES(1,7)")
    conn.commit()


def test_publication_requires_user_confirmation_and_creates_review_once():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _tables(conn)
    conn.execute("UPDATE drafts SET user_confirmed_at=NULL")
    conn.commit()
    with pytest.raises(ClosedLoopError):
        verify_publication(conn,draft_id="d1",platform_content_id="p1",
            platform_url="https://platform.example/p/1",published_at="2026-11-04T10:00:00",
            content_snapshot={"title":"标题"})
    conn.execute("UPDATE drafts SET user_confirmed_at='2026-11-04T10:00:00'")
    conn.commit()
    verify_publication(conn,draft_id="d1",platform_content_id="p1",
        platform_url="https://platform.example/p/1",published_at="2026-11-04T10:00:00",
        content_snapshot={"title":"标题"})
    with pytest.raises(ClosedLoopError):
        verify_publication(conn,draft_id="d1",platform_content_id="p2",
            platform_url="https://platform.example/p/2",published_at="2026-11-04T10:00:00",
            content_snapshot={"title":"标题"})
    assert conn.execute("SELECT COUNT(*) FROM review_tasks").fetchone()[0] == 1
    review = conn.execute("SELECT collect_after,window_start FROM review_tasks").fetchone()
    assert review["collect_after"] == "2026-11-05T10:00:00"
    assert review["window_start"] == "2026-11-04T10:00:00"
    assert conn.execute("SELECT COUNT(*) FROM closed_loop_events").fetchone()[0] == 1


def test_failed_collection_is_not_zero_and_can_be_retried():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _tables(conn)
    conn.execute("""INSERT INTO publish_verifications VALUES
      ('v1','d1','verified','p1','https://platform.example/p/1','2026-11-04T10:00:00','{}','2026-11-04T10:00:00','user','2026-11-04T10:00:00')""")
    conn.execute("""INSERT INTO review_tasks
      (id,draft_id,publish_verification_id,status,collection_source,collect_after,window_start,
       platform_content_id,publish_snapshot_json,created_at,updated_at)
      VALUES('r1','d1','v1','scheduled','manual','2026-11-05','2026-11-04','p1','{}','now','now')""")
    conn.commit()
    failed = record_review_collection(conn,task_id="r1",result={},status="failed",error="暂不可用")
    assert failed["status"] == "failed"
    assert json.loads(failed["collection_result_json"]) == {}
    done = record_review_collection(conn,task_id="r1",result={"views":0},status="completed")
    assert json.loads(done["collection_result_json"])["views"] == 0


def test_visual_revision_requires_reason_and_full_style_for_single_page():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE contents(id TEXT PRIMARY KEY); INSERT INTO contents VALUES('c1'); CREATE TABLE visual_revision_audits(id TEXT PRIMARY KEY,content_id TEXT,base_visual_version_id INTEGER,generated_visual_version_id INTEGER,scope TEXT,target_page_index INTEGER,original_reason TEXT,normalized_instruction TEXT,final_prompt TEXT,style_reference_json TEXT,preserved_page_hashes_json TEXT,status TEXT,created_at TEXT,created_by TEXT);")
    with pytest.raises(ClosedLoopError):
        create_visual_revision_audit(conn,content_id="c1",scope="single_page",reason="",
            normalized_instruction="改标题",final_prompt="prompt",style_reference={"visual_language":"flat"},
            target_page_index=1)
    style = {k:"x" for k in ("visual_language","primary_colors","secondary_colors","background",
        "typography","layout","aspect_ratio","whitespace","hierarchy","rendering_style","neighboring_pages")}
    audit = create_visual_revision_audit(conn,content_id="c1",scope="single_page",reason="标题不清楚",
        normalized_instruction="增强标题对比度",final_prompt="keep style; improve title",style_reference=style,
        target_page_index=1,preserved_page_hashes={"0":"abc","2":"def"})
    assert audit["status"] == "pending"
    assert json.loads(audit["preserved_page_hashes_json"]) == {"0":"abc","2":"def"}


def test_gap_history_appends_instead_of_overwriting():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _tables(conn)
    create_gap_snapshot(conn,gap_key="audience",status="open",description="目标受众不清晰")
    create_gap_snapshot(conn,gap_key="audience",status="shrinking",description="画像已收敛")
    assert conn.execute("SELECT COUNT(*) FROM business_gap_snapshots WHERE gap_key='audience'").fetchone()[0] == 2

def test_publication_rejects_invalid_timestamp_before_writing():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _tables(conn)
    with pytest.raises(ClosedLoopError, match="ISO 8601"):
        verify_publication(conn,draft_id="d1",platform_content_id="p1",
            platform_url="https://platform.example/p/1",published_at="not-a-date",
            content_snapshot={"title":"标题"})
    assert conn.execute("SELECT COUNT(*) FROM publish_verifications").fetchone()[0] == 0
