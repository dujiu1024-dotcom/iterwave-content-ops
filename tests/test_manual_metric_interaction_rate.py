from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from src.core.autogrow import build_feedback


def _manual_snapshot(conn, *, platform, dimension, title, views, likes, collects, comments,
                     correction_of=None, measured_at=None, content_id=None, draft_id=None):
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    if content_id is None or draft_id is None:
        topic_id = f"topic-{uuid.uuid4().hex}"
        content_id = f"content-{uuid.uuid4().hex}"
        draft_id = f"draft-{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO topic_index(id,dimension,source,title) VALUES(?,?,'test',?)",
            (topic_id, dimension, title),
        )
        round_id = conn.execute(
            "SELECT id FROM spiral_rounds ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO contents(id,round_id,topic_id,platform,title,body) VALUES(?,?,?,?,?,'body')",
            (content_id, round_id, topic_id, platform, title),
        )
        conn.execute(
            "INSERT INTO drafts(id,content_id,platform,status) VALUES(?,?,?,'published')",
            (draft_id, content_id, platform),
        )
    else:
        round_id = conn.execute(
            "SELECT round_id FROM contents WHERE id=?", (content_id,)
        ).fetchone()[0]
    snapshot_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO metric_snapshots(id,tenant_id,draft_id,content_id,platform,source,"
        "views,likes,collects,comments,measured_at,recorded_at,correction_of,created_at) "
        "VALUES(?, 'default', ?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_id, draft_id, content_id, platform, views, likes, collects, comments,
         measured_at or now, now, correction_of, now),
    )
    return content_id, snapshot_id, round_id


def test_manual_metric_snapshots_create_platform_correct_interaction_rate(cfg, conn, seed):
    seed.round(status="completed")
    xhs_content, _, _ = _manual_snapshot(
        conn, platform="xhs", dimension="AI教育", title="xhs manual",
        views=100, likes=10, collects=5, comments=5,
    )
    douyin_content, _, _ = _manual_snapshot(
        conn, platform="douyin", dimension="AI教育", title="douyin manual",
        views=100, likes=10, collects=None, comments=5,
    )
    missing_content, _, _ = _manual_snapshot(
        conn, platform="xhs", dimension="生活", title="missing collects",
        views=100, likes=10, collects=None, comments=5,
    )
    conn.commit()

    values = {
        row["content_id"]: row["value"]
        for row in conn.execute(
            "SELECT content_id,value FROM metric_facts_latest "
            "WHERE metric_key='interaction_rate' AND content_id IN (?,?,?)",
            (xhs_content, douyin_content, missing_content),
        ).fetchall()
    }
    assert values[xhs_content] == 0.2
    assert values[douyin_content] == 0.15
    assert missing_content not in values


def test_manual_metric_correction_replaces_old_interaction_rate_fact(conn, seed):
    seed.round(status="completed")
    content_id, original_id, _ = _manual_snapshot(
        conn, platform="xhs", dimension="AI教育", title="manual correction",
        views=100, likes=10, collects=5, comments=5,
    )
    later = (datetime.now() + timedelta(seconds=1)).isoformat(sep=" ", timespec="seconds")
    _manual_snapshot(
        conn, platform="xhs", dimension="AI教育", title="manual correction",
        views=100, likes=20, collects=5, comments=5,
        correction_of=original_id, measured_at=later,
        content_id=content_id,
        draft_id=conn.execute("SELECT id FROM drafts WHERE content_id=?", (content_id,)).fetchone()[0],
    )
    conn.commit()

    effective = conn.execute(
        "SELECT value FROM metric_facts_effective WHERE content_id=? "
        "AND metric_key='interaction_rate' ORDER BY measured_at DESC LIMIT 1",
        (content_id,),
    ).fetchone()
    assert effective is not None
    assert effective["value"] == 0.3
    latest = conn.execute(
        "SELECT value FROM metric_facts_latest WHERE content_id=? AND metric_key='interaction_rate'",
        (content_id,),
    ).fetchone()
    assert latest["value"] == 0.3


def test_autogrow_feedback_uses_manual_interaction_rate_facts(cfg, conn, seed):
    round_id = seed.round(status="completed")
    decision = {
        "strategy_adjustments": [],
        "gaps": [],
        "platform_insights": [],
    }
    conn.execute(
        "UPDATE spiral_rounds SET decision=? WHERE id=?",
        (json.dumps(decision), round_id),
    )
    _manual_snapshot(
        conn, platform="xhs", dimension="AI教育", title="high manual",
        views=100, likes=20, collects=10, comments=10,
    )
    _manual_snapshot(
        conn, platform="xhs", dimension="生活", title="low manual",
        views=100, likes=2, collects=1, comments=2,
    )
    conn.commit()

    feedback = build_feedback(cfg, conn)
    assert feedback.source_round_ids == [round_id]
    assert feedback.dimension_bias["AI教育"] > 0
    assert feedback.dimension_bias["生活"] < 0

    from src.core.spiral import SpiralEngine
    spiral_metrics = SpiralEngine(cfg, conn)._fetch_published_metrics(round_id)
    assert len(spiral_metrics) == 2
    by_dimension = {row["dimension"]: row for row in spiral_metrics}
    assert by_dimension["AI教育"]["interaction_rate"] == 0.4
    assert by_dimension["AI教育"]["manual_metrics"] is True
    assert by_dimension["生活"]["interaction_rate"] == 0.05
