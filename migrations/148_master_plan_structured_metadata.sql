-- 148: structured, versioned AI provenance and effective metadata for master plans.
ALTER TABLE master_plan_versions ADD COLUMN ai_metadata_json TEXT;
ALTER TABLE master_plan_versions ADD COLUMN effective_metadata_json TEXT;
ALTER TABLE master_plan_versions ADD COLUMN metadata_signature TEXT;
ALTER TABLE master_plan_versions ADD COLUMN metadata_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE master_plan_versions ADD COLUMN metadata_updated_at TEXT;
ALTER TABLE master_plan_versions ADD COLUMN metadata_updated_by TEXT;

CREATE TABLE IF NOT EXISTS master_plan_metadata_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_plan_version_id INTEGER NOT NULL REFERENCES master_plan_versions(id),
    metadata_version INTEGER NOT NULL,
    ai_metadata_json TEXT,
    effective_metadata_json TEXT NOT NULL,
    metadata_signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    change_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(master_plan_version_id, metadata_version)
);
CREATE INDEX IF NOT EXISTS idx_master_plan_metadata_revisions_version
    ON master_plan_metadata_revisions(master_plan_version_id, metadata_version DESC);
