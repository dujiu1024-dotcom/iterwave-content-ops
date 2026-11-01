-- Persist per-source single-fetch diagnostics without storing source URLs or secrets.
CREATE TABLE IF NOT EXISTS hot_source_test_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('rss', 'json')),
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'empty')),
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    sample_titles_json TEXT NOT NULL DEFAULT '[]',
    message TEXT NOT NULL DEFAULT '',
    tested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hot_source_test_history_key_id
    ON hot_source_test_history(source_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_hot_source_test_history_tested_at
    ON hot_source_test_history(tested_at DESC);
