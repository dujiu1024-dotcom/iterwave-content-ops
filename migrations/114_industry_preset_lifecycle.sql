-- P1-06: preserve custom industry preset lifecycle and immutable snapshots.
ALTER TABLE industry_presets ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1));
ALTER TABLE industry_presets ADD COLUMN archived_at TEXT;
CREATE TABLE IF NOT EXISTS industry_preset_versions (preset_key TEXT NOT NULL, version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(preset_key, version));
CREATE INDEX IF NOT EXISTS idx_industry_preset_versions_key ON industry_preset_versions(preset_key, version DESC);
