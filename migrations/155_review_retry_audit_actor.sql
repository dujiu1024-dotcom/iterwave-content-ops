-- 155: retain the actor for explicit manual review-task retries.
ALTER TABLE review_tasks ADD COLUMN last_retried_by TEXT;
CREATE INDEX IF NOT EXISTS idx_review_tasks_retry_actor
    ON review_tasks(tenant_id, last_retried_by, updated_at DESC);
