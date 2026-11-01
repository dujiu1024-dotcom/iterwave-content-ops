-- Template lifecycle, immutable versions and content-time snapshots.
ALTER TABLE content_assets ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE content_assets ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1));
ALTER TABLE contents ADD COLUMN template_version INTEGER;
ALTER TABLE contents ADD COLUMN template_snapshot_json TEXT;

CREATE TABLE IF NOT EXISTS content_template_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES content_assets(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    body TEXT,
    created_at DATETIME NOT NULL,
    UNIQUE(template_id, version)
);

INSERT OR IGNORE INTO content_template_versions (template_id, version, title, body, created_at)
SELECT id, 1, title, body, COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
FROM content_assets WHERE asset_type = 'template';
