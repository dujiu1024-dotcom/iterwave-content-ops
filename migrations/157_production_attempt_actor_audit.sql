-- 157: persist trusted queue initiator and executing service identity on each production attempt.
-- NULL means the task was claimed outside the global Worker identity context.
ALTER TABLE production_task_attempts ADD COLUMN initiating_actor TEXT;
ALTER TABLE production_task_attempts ADD COLUMN executing_actor TEXT;
ALTER TABLE production_task_attempts ADD COLUMN tenant_id TEXT;
ALTER TABLE production_task_attempts ADD COLUMN queue_task_id TEXT;
