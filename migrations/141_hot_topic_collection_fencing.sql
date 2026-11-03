-- 140: cross-worker fencing and idempotent hot-topic snapshot commits.
CREATE TABLE IF NOT EXISTS hot_topic_source_leases (
    source TEXT PRIMARY KEY,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    owner TEXT,
    lease_expires_at INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hot_topic_collection_commits (
    source TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    committed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, collection_id)
);

CREATE INDEX IF NOT EXISTS idx_hot_topic_source_leases_expiry
    ON hot_topic_source_leases(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_hot_topic_collection_commits_time
    ON hot_topic_collection_commits(committed_at);
