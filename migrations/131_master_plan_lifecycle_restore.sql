-- 131: master-plan soft deletion, predecessor restoration and child pause provenance.
-- Historical production/round rows remain immutable and are never cascaded.

ALTER TABLE master_plan_versions ADD COLUMN deleted_at TEXT;
ALTER TABLE master_plan_versions ADD COLUMN deleted_by TEXT;
ALTER TABLE sub_plans ADD COLUMN pause_reason TEXT
    CHECK(pause_reason IS NULL OR pause_reason IN ('user','master_plan_switch','master_plan_deleted'));
ALTER TABLE sub_plans ADD COLUMN paused_by_master_version_id INTEGER
    REFERENCES master_plan_versions(id);

CREATE INDEX IF NOT EXISTS idx_master_plan_undeleted_version
    ON master_plan_versions(version_no DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_subplan_master_pause_provenance
    ON sub_plans(master_plan_version_id,status,pause_reason,paused_by_master_version_id);

-- Enforce latest-only activation at the database boundary as well as in the store.
CREATE TRIGGER IF NOT EXISTS trg_master_plan_latest_only_insert
BEFORE INSERT ON master_plan_versions
WHEN NEW.status='effective'
 AND (NEW.deleted_at IS NOT NULL OR NEW.id IS NOT (
     SELECT id FROM master_plan_versions
     WHERE deleted_at IS NULL
       AND (status IN ('draft','effective') OR effective_at IS NOT NULL)
     ORDER BY version_no DESC LIMIT 1
 ))
BEGIN
    SELECT RAISE(ABORT,'only latest undeleted master plan may be effective');
END;

CREATE TRIGGER IF NOT EXISTS trg_master_plan_latest_only_update
BEFORE UPDATE OF status,deleted_at,version_no ON master_plan_versions
WHEN NEW.status='effective'
 AND (NEW.deleted_at IS NOT NULL OR NEW.id IS NOT (
     SELECT id FROM master_plan_versions
     WHERE deleted_at IS NULL
       AND (status IN ('draft','effective') OR effective_at IS NOT NULL)
     ORDER BY version_no DESC LIMIT 1
 ))
BEGIN
    SELECT RAISE(ABORT,'only latest undeleted master plan may be effective');
END;

CREATE TRIGGER IF NOT EXISTS trg_master_plan_no_delete_effective
BEFORE UPDATE OF deleted_at ON master_plan_versions
WHEN NEW.deleted_at IS NOT NULL AND OLD.status='effective' AND NEW.status='effective'
BEGIN
    SELECT RAISE(ABORT,'effective master plan must be archived before soft deletion');
END;
