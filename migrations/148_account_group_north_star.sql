-- 148: independent account-group North Star versions and immutable plan/resource baselines.
CREATE TABLE IF NOT EXISTS account_group_north_star_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 version_no INTEGER NOT NULL UNIQUE CHECK(version_no > 0),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
 status TEXT NOT NULL DEFAULT 'effective' CHECK(status IN ('effective','archived')),
 change_reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 effective_at TEXT NOT NULL, superseded_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_north_star_one_effective ON account_group_north_star_versions(status) WHERE status='effective';
CREATE INDEX IF NOT EXISTS idx_north_star_history ON account_group_north_star_versions(version_no DESC,created_at DESC);
ALTER TABLE master_plan_versions ADD COLUMN north_star_version_id INTEGER REFERENCES account_group_north_star_versions(id) ON DELETE RESTRICT;
ALTER TABLE master_plan_versions ADD COLUMN north_star_snapshot_json TEXT;
ALTER TABLE master_plan_versions ADD COLUMN north_star_attribution_status TEXT NOT NULL DEFAULT 'legacy_unattributed' CHECK(north_star_attribution_status IN ('attributed','legacy_unattributed','manual_review'));
CREATE TABLE IF NOT EXISTS master_plan_resource_baselines (
 master_plan_version_id INTEGER PRIMARY KEY REFERENCES master_plan_versions(id) ON DELETE RESTRICT,
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), captured_at TEXT NOT NULL, captured_by TEXT NOT NULL DEFAULT 'system'
);
CREATE TRIGGER IF NOT EXISTS trg_north_star_version_snapshot_immutable BEFORE UPDATE OF version_no,snapshot_json,change_reason,created_by,created_at,effective_at ON account_group_north_star_versions BEGIN SELECT RAISE(ABORT,'north-star version content is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_north_star_version_no_delete BEFORE DELETE ON account_group_north_star_versions BEGIN SELECT RAISE(ABORT,'north-star version history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_master_plan_resource_baselines_no_update BEFORE UPDATE ON master_plan_resource_baselines BEGIN SELECT RAISE(ABORT,'master plan resource baselines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_master_plan_resource_baselines_no_delete BEFORE DELETE ON master_plan_resource_baselines BEGIN SELECT RAISE(ABORT,'master plan resource baselines are immutable'); END;
