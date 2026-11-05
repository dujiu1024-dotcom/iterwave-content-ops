-- 157: retain the authenticated principal responsible for subgoal authoring and production-plan edits.
-- Historical rows remain explicitly unattributed; do not infer a human actor retroactively.
ALTER TABLE sub_goals ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE sub_goals ADD COLUMN updated_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE sub_goal_production_plans ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE sub_goal_production_plans ADD COLUMN updated_by TEXT NOT NULL DEFAULT 'legacy:unattributed';

CREATE INDEX IF NOT EXISTS idx_sub_goals_created_by
    ON sub_goals(sub_plan_version_id,created_by,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_goal_plans_updated_by
    ON sub_goal_production_plans(updated_by,updated_at DESC);

-- Append-only actor/context records preserve both the initiating principal and the
-- executing service identity. The tenant database remains the authorization boundary.
CREATE TABLE IF NOT EXISTS sub_goal_audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    action TEXT NOT NULL,
    sub_plan_id TEXT NOT NULL,
    sub_plan_version_id INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    initiating_actor TEXT NOT NULL,
    executing_actor TEXT NOT NULL,
    task_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_goal_audit_tenant_goal
    ON sub_goal_audit_events(tenant_id,goal_id,created_at DESC,event_id DESC);
CREATE INDEX IF NOT EXISTS idx_sub_goal_audit_task
    ON sub_goal_audit_events(tenant_id,task_id,created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_sub_goal_audit_no_update
BEFORE UPDATE ON sub_goal_audit_events BEGIN
    SELECT RAISE(ABORT, 'sub_goal_audit_events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_sub_goal_audit_no_delete
BEFORE DELETE ON sub_goal_audit_events BEGIN
    SELECT RAISE(ABORT, 'sub_goal_audit_events are append-only');
END;

ALTER TABLE content_production_line_events ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE content_production_line_events ADD COLUMN actor_id TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE content_production_line_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE content_production_line_events ADD COLUMN initiating_actor TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE content_production_line_events ADD COLUMN executing_actor TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE content_production_line_events ADD COLUMN task_id TEXT;

ALTER TABLE production_context_waivers ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE production_context_waivers ADD COLUMN actor_id TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE production_context_waivers ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE production_context_waivers ADD COLUMN initiating_actor TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE production_context_waivers ADD COLUMN executing_actor TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE production_context_waivers ADD COLUMN task_id TEXT;
CREATE INDEX IF NOT EXISTS idx_production_context_waiver_actor
    ON production_context_waivers(tenant_id,actor_type,actor_id,created_at DESC);
