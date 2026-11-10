-- Persist the durable queue handoff so repeated promotion requests cannot enqueue
-- a second formal production task after the first queue task has completed.
ALTER TABLE sample_contents ADD COLUMN promoted_task_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activation_sample_promoted_task
    ON sample_contents(promoted_task_id) WHERE promoted_task_id IS NOT NULL;
