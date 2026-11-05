-- 158: persist trusted async Worker identity on immutable plan-change audit events.
ALTER TABLE plan_change_audit ADD COLUMN initiating_actor TEXT;
ALTER TABLE plan_change_audit ADD COLUMN executing_actor TEXT;
ALTER TABLE plan_change_audit ADD COLUMN tenant_id TEXT;
ALTER TABLE plan_change_audit ADD COLUMN queue_task_id TEXT;
