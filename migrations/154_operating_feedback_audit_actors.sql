-- 154: auditable attribution for publication, review collection and business-gap actions.
-- Existing rows are explicitly marked historical/system; no actor is inferred retroactively.
ALTER TABLE content_publish_verifications
    ADD COLUMN verified_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE review_tasks
    ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE review_tasks
    ADD COLUMN conclusion_by TEXT;
ALTER TABLE review_collection_runs
    ADD COLUMN recorded_by TEXT NOT NULL DEFAULT 'system:review-worker';
ALTER TABLE business_gap_events
    ADD COLUMN actor TEXT NOT NULL DEFAULT 'system:review-worker';

CREATE INDEX IF NOT EXISTS idx_publish_verification_actor
    ON content_publish_verifications(tenant_id, verified_by, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_tasks_creator
    ON review_tasks(tenant_id, created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_runs_actor
    ON review_collection_runs(recorded_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_gap_events_actor
    ON business_gap_events(actor, created_at DESC);
