-- Retry-safe idempotency for manual metric snapshot submissions.
-- Existing rows remain historical records with NULL idempotency keys.
ALTER TABLE metric_snapshots ADD COLUMN idempotency_key TEXT;
ALTER TABLE metric_snapshots ADD COLUMN payload_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_metric_snapshots_idempotency
    ON metric_snapshots(tenant_id, draft_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
