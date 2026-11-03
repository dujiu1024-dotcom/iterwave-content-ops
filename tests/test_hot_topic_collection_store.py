from __future__ import annotations

import time
import pytest
from src.core.db import Database
from src.core.hot_topic_collection_store import claim_source, commit_snapshot


def test_two_connections_serialize_source_claims(cfg, conn):
    other = Database(cfg).connect()
    try:
        state_a, claim_a = claim_source(conn, "source-a", "round-a", "worker-a", 120)
        state_b, claim_b = claim_source(other, "source-a", "round-b", "worker-b", 120)
        assert state_a == "claimed" and claim_a is not None
        assert state_b == "busy" and claim_b is None
    finally:
        other.close()


def test_expired_lease_rejects_late_commit(cfg, conn):
    other = Database(cfg).connect()
    try:
        _, old_claim = claim_source(conn, "source-b", "round-old", "worker-old", 120)
        conn.execute("UPDATE hot_topic_source_leases SET lease_expires_at=? WHERE source=?",
                     (int(time.time()) - 1, "source-b"))
        conn.commit()
        _, new_claim = claim_source(other, "source-b", "round-new", "worker-new", 120)
        with pytest.raises(RuntimeError, match="租约已失效"):
            commit_snapshot(conn, old_claim, 1, lambda: conn.execute(
                "INSERT INTO hot_topic_collection_commits(source,collection_id,fencing_token,item_count) "
                "VALUES('source-b','stale',1,1)"
            ))
        assert commit_snapshot(other, new_claim, 1, lambda: None)
        assert conn.execute("SELECT collection_id FROM hot_topic_collection_commits WHERE source='source-b'").fetchone()[0] == "round-new"
    finally:
        other.close()


def test_collection_replay_is_idempotent_and_next_round_is_allowed(conn):
    _, claim = claim_source(conn, "source-c", "round-one", "worker-a", 120)
    calls = []
    assert commit_snapshot(conn, claim, 1, lambda: calls.append(1))
    state, duplicate = claim_source(conn, "source-c", "round-one", "worker-b", 120)
    assert state == "duplicate" and duplicate is None
    _, next_claim = claim_source(conn, "source-c", "round-two", "worker-c", 120)
    assert commit_snapshot(conn, next_claim, 1, lambda: calls.append(2))
    assert calls == [1, 2]


def test_collector_replay_does_not_increment_seen_count_but_next_round_does(cfg, conn, monkeypatch):
    from src.core.hot_topics import HotTopicCollector

    collector = HotTopicCollector(cfg, conn)
    collector.aggregate_sources = [{"name": "60s.viki.moe", "platforms": ["weibo"]}]
    collector.custom_sources = []
    monkeypatch.setattr(
        collector, "_fetch_platform",
        lambda *args, **kwargs: [{
            "id": "hot-cross-worker-round",
            "title": "跨 Worker 幂等热点",
            "raw_url": "https://example.com/topic",
            "heat_score": 100,
            "heat_unit": "热度",
            "time_sensitive": None,
        }],
    )

    first = collector.refresh(collection_id="same-round")
    replay = collector.refresh(collection_id="same-round")
    next_round = collector.refresh(collection_id="next-round")

    assert first["60s.viki.moe/weibo"] == "collected"
    assert replay["60s.viki.moe/weibo"] == "duplicate"
    assert next_round["60s.viki.moe/weibo"] == "collected"
    row = conn.execute(
        "SELECT seen_count FROM hot_topics WHERE id='hot-cross-worker-round'"
    ).fetchone()
    assert row["seen_count"] == 2
