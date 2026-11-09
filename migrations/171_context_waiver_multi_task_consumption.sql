-- 171: allow one round/platform business-context waiver to cover multiple
-- independently persisted content tasks while retaining per-task consumption evidence.
CREATE TABLE IF NOT EXISTS production_context_waiver_consumptions (
    waiver_id TEXT NOT NULL REFERENCES production_context_waivers(id) ON DELETE RESTRICT,
    production_task_id TEXT NOT NULL UNIQUE REFERENCES production_tasks(id) ON DELETE RESTRICT,
    round_id INTEGER NOT NULL REFERENCES spiral_rounds(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL,
    consumed_at TEXT NOT NULL,
    PRIMARY KEY(waiver_id, production_task_id)
);
CREATE INDEX IF NOT EXISTS idx_context_waiver_consumptions_round_platform
    ON production_context_waiver_consumptions(round_id, platform, consumed_at DESC);

-- Preserve first-consumer links created by migration 150.
INSERT OR IGNORE INTO production_context_waiver_consumptions(
    waiver_id,production_task_id,round_id,platform,consumed_at
)
SELECT id,consumed_task_id,round_id,platform,CURRENT_TIMESTAMP
FROM production_context_waivers
WHERE consumed_task_id IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS trg_context_waiver_consumptions_no_update
BEFORE UPDATE ON production_context_waiver_consumptions
BEGIN
    SELECT RAISE(ABORT, 'production_context_waiver_consumptions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_context_waiver_consumptions_no_delete
BEFORE DELETE ON production_context_waiver_consumptions
BEGIN
    SELECT RAISE(ABORT, 'production_context_waiver_consumptions are append-only');
END;
