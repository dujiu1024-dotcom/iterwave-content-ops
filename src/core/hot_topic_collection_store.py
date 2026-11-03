"""SQLite-backed cross-worker leases and fenced hot-topic snapshot commits."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class SourceClaim:
    source: str
    collection_id: str
    owner: str
    fencing_token: int
    lease_expires_at: int


def _begin(conn) -> None:
    if conn.in_transaction:
        raise RuntimeError("热点租约操作要求独立事务边界")
    conn.execute("BEGIN IMMEDIATE")


def claim_source(conn, source: str, collection_id: str, owner: str,
                 lease_seconds: int = 300) -> tuple[str, SourceClaim | None]:
    """Return claimed, duplicate, or busy. Token increases on every lease takeover."""
    now = int(time.time())
    expires = now + max(10, int(lease_seconds))
    try:
        _begin(conn)
        committed = conn.execute(
            "SELECT 1 FROM hot_topic_collection_commits WHERE source=? AND collection_id=?",
            (source, collection_id),
        ).fetchone()
        if committed:
            conn.commit()
            return "duplicate", None
        row = conn.execute(
            "SELECT fencing_token, owner, lease_expires_at FROM hot_topic_source_leases WHERE source=?",
            (source,),
        ).fetchone()
        if row and row["owner"] and int(row["lease_expires_at"] or 0) > now:
            conn.commit()
            return "busy", None
        token = (int(row["fencing_token"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO hot_topic_source_leases(source,fencing_token,owner,lease_expires_at,updated_at) "
            "VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(source) DO UPDATE SET "
            "fencing_token=excluded.fencing_token, owner=excluded.owner, "
            "lease_expires_at=excluded.lease_expires_at, updated_at=CURRENT_TIMESTAMP",
            (source, token, owner, expires),
        )
        conn.commit()
        return "claimed", SourceClaim(source, collection_id, owner, token, expires)
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _assert_current(conn, claim: SourceClaim, now: int) -> None:
    row = conn.execute(
        "SELECT fencing_token, owner, lease_expires_at FROM hot_topic_source_leases WHERE source=?",
        (claim.source,),
    ).fetchone()
    if (not row or int(row["fencing_token"]) != claim.fencing_token
            or row["owner"] != claim.owner
            or int(row["lease_expires_at"] or 0) <= now):
        raise RuntimeError("热点采集租约已失效，拒绝提交过期快照")


def commit_snapshot(conn, claim: SourceClaim, item_count: int,
                    apply_snapshot: Callable[[], None]) -> bool:
    """Apply upsert + snapshot deletion + idempotency record in one fenced transaction."""
    try:
        _begin(conn)
        _assert_current(conn, claim, int(time.time()))
        exists = conn.execute(
            "SELECT 1 FROM hot_topic_collection_commits WHERE source=? AND collection_id=?",
            (claim.source, claim.collection_id),
        ).fetchone()
        if exists:
            conn.rollback()
            return False
        apply_snapshot()
        conn.execute(
            "INSERT INTO hot_topic_collection_commits(source,collection_id,fencing_token,item_count) "
            "VALUES(?,?,?,?)",
            (claim.source, claim.collection_id, claim.fencing_token, max(0, int(item_count))),
        )
        conn.execute(
            "UPDATE hot_topic_source_leases SET owner=NULL, lease_expires_at=0, "
            "updated_at=CURRENT_TIMESTAMP WHERE source=? AND fencing_token=? AND owner=?",
            (claim.source, claim.fencing_token, claim.owner),
        )
        conn.commit()
        return True
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def fail_source(conn, claim: SourceClaim, record_failure: Callable[[], None]) -> bool:
    """Record a failed source only while its lease is current; failures remain retryable."""
    try:
        _begin(conn)
        _assert_current(conn, claim, int(time.time()))
        record_failure()
        conn.execute(
            "UPDATE hot_topic_source_leases SET owner=NULL, lease_expires_at=0, "
            "updated_at=CURRENT_TIMESTAMP WHERE source=? AND fencing_token=? AND owner=?",
            (claim.source, claim.fencing_token, claim.owner),
        )
        conn.commit()
        return True
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def release_source(conn, claim: SourceClaim) -> bool:
    try:
        _begin(conn)
        _assert_current(conn, claim, int(time.time()))
        conn.execute(
            "UPDATE hot_topic_source_leases SET owner=NULL, lease_expires_at=0, "
            "updated_at=CURRENT_TIMESTAMP WHERE source=? AND fencing_token=? AND owner=?",
            (claim.source, claim.fencing_token, claim.owner),
        )
        conn.commit()
        return True
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
