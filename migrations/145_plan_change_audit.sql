-- 145: immutable plan-change audit and task-impact snapshots.
CREATE TABLE IF NOT EXISTS plan_change_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL
        CHECK (entity_type IN ('master_plan','sub_plan','production_line')),
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('draft_created','version_approved','version_rejected','version_deleted','version_restored')),
    from_version_id INTEGER,
    to_version_id INTEGER NOT NULL,
    change_reason TEXT NOT NULL DEFAULT '',
    before_snapshot_json TEXT,
    after_snapshot_json TEXT NOT NULL,
    affected_tasks_json TEXT NOT NULL DEFAULT '{"tasks":[],"counts":{}}',
    actor TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_change_audit_entity
    ON plan_change_audit(entity_type, entity_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_plan_change_audit_versions
    ON plan_change_audit(entity_type, from_version_id, to_version_id);

CREATE TRIGGER IF NOT EXISTS trg_plan_change_audit_no_update
BEFORE UPDATE ON plan_change_audit
BEGIN
    SELECT RAISE(ABORT, 'plan_change_audit is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_plan_change_audit_no_delete
BEFORE DELETE ON plan_change_audit
BEGIN
    SELECT RAISE(ABORT, 'plan_change_audit is append-only');
END;
