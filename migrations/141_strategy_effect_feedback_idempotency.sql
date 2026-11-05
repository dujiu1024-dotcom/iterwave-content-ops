-- 141: idempotent strategy effect feedback submissions.
-- Keys are scoped to a strategy stage; NULL preserves compatibility for legacy clients.
ALTER TABLE strategy_effect_reviews ADD COLUMN idempotency_key TEXT;
ALTER TABLE strategy_effect_reviews ADD COLUMN request_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_effect_reviews_idempotency
    ON strategy_effect_reviews(stage_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
