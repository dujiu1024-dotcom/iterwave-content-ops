-- 162: persist executing service and queue correlation alongside operating-feedback actors.
ALTER TABLE content_publish_verifications ADD COLUMN verified_executing_actor TEXT;
ALTER TABLE content_publish_verifications ADD COLUMN verified_queue_task_id TEXT;

ALTER TABLE production_line_close_outbox ADD COLUMN created_by TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN created_executing_actor TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN created_queue_task_id TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN claimed_initiating_actor TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN claimed_executing_actor TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN claimed_queue_task_id TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN acknowledged_by TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN acknowledged_executing_actor TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN acknowledged_queue_task_id TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN last_retried_by TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN last_retry_executing_actor TEXT;
ALTER TABLE production_line_close_outbox ADD COLUMN last_retry_queue_task_id TEXT;

ALTER TABLE review_tasks ADD COLUMN created_executing_actor TEXT;
ALTER TABLE review_tasks ADD COLUMN created_queue_task_id TEXT;
ALTER TABLE review_tasks ADD COLUMN conclusion_executing_actor TEXT;
ALTER TABLE review_tasks ADD COLUMN conclusion_queue_task_id TEXT;
ALTER TABLE review_tasks ADD COLUMN last_retry_executing_actor TEXT;
ALTER TABLE review_tasks ADD COLUMN last_retry_queue_task_id TEXT;

ALTER TABLE review_collection_runs ADD COLUMN recorded_executing_actor TEXT;
ALTER TABLE review_collection_runs ADD COLUMN recorded_queue_task_id TEXT;
ALTER TABLE business_gap_events ADD COLUMN executing_actor TEXT;
ALTER TABLE business_gap_events ADD COLUMN queue_task_id TEXT;

CREATE INDEX IF NOT EXISTS idx_publish_verification_execution
    ON content_publish_verifications(tenant_id,verified_executing_actor,verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_task_execution
    ON review_tasks(tenant_id,created_executing_actor,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_collection_execution
    ON review_collection_runs(recorded_executing_actor,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_gap_execution
    ON business_gap_events(executing_actor,created_at DESC);
