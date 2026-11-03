-- 142: unify legacy content-plan batches with the canonical strategy provenance chain.
-- ContentPlan remains a historical batch/schedule projection; it is not a second
-- strategic-plan model. Each linked round receives a frozen attribution snapshot.
-- Snapshot columns intentionally have no FK constraints: preserve orphaned legacy IDs
-- for manual reconciliation instead of making an upgrade fail or erasing evidence.
ALTER TABLE content_plan_rounds ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE content_plan_rounds ADD COLUMN sub_plan_id TEXT;
ALTER TABLE content_plan_rounds ADD COLUMN sub_plan_version_id INTEGER;
ALTER TABLE content_plan_rounds ADD COLUMN sub_plan_stage_id INTEGER;
ALTER TABLE content_plan_rounds ADD COLUMN strategy_step_id TEXT;
ALTER TABLE content_plan_rounds ADD COLUMN production_line_version_id INTEGER;
ALTER TABLE content_plan_rounds ADD COLUMN attribution_status TEXT NOT NULL
    DEFAULT 'legacy_unattributed'
    CHECK (attribution_status IN ('attributed','manual_review','legacy_unattributed'));

UPDATE content_plan_rounds
SET
    master_plan_version_id = (
        SELECT r.master_plan_version_id FROM spiral_rounds r
        WHERE r.id = content_plan_rounds.round_id
    ),
    sub_plan_id = (
        SELECT r.sub_plan_id FROM spiral_rounds r
        WHERE r.id = content_plan_rounds.round_id
    ),
    sub_plan_version_id = (
        SELECT r.sub_plan_version_id FROM spiral_rounds r
        WHERE r.id = content_plan_rounds.round_id
    ),
    sub_plan_stage_id = (
        SELECT r.sub_plan_stage_id FROM spiral_rounds r
        WHERE r.id = content_plan_rounds.round_id
    ),
    strategy_step_id = (
        SELECT s.stage_key
        FROM spiral_rounds r
        JOIN sub_plan_stages s ON s.id = r.sub_plan_stage_id
            AND s.sub_plan_version_id = r.sub_plan_version_id
        WHERE r.id = content_plan_rounds.round_id
    ),
    production_line_version_id = (
        SELECT r.production_line_version_id FROM spiral_rounds r
        WHERE r.id = content_plan_rounds.round_id
    ),
    attribution_status = CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM spiral_rounds r WHERE r.id = content_plan_rounds.round_id
        ) THEN 'legacy_unattributed'
        WHEN EXISTS (
            SELECT 1
            FROM spiral_rounds r
            JOIN master_plan_versions mp ON mp.id = r.master_plan_version_id
            JOIN sub_plans p ON p.id = r.sub_plan_id
                AND p.master_plan_version_id = r.master_plan_version_id
            JOIN sub_plan_versions sv ON sv.id = r.sub_plan_version_id
                AND sv.sub_plan_id = p.id
                AND sv.master_plan_version_id = r.master_plan_version_id
            JOIN sub_plan_stages s ON s.id = r.sub_plan_stage_id
                AND s.sub_plan_version_id = sv.id
            JOIN production_line_versions plv ON plv.id = r.production_line_version_id
                AND plv.sub_plan_version_id = sv.id
            JOIN production_lines pl ON pl.id = plv.production_line_id
                AND pl.sub_plan_id = p.id
            WHERE r.id = content_plan_rounds.round_id
              AND s.stage_key IS NOT NULL
        ) THEN 'attributed'
        ELSE 'manual_review'
    END;

CREATE INDEX IF NOT EXISTS idx_content_plan_rounds_master_version
    ON content_plan_rounds(master_plan_version_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_plan_rounds_strategy
    ON content_plan_rounds(sub_plan_id, sub_plan_version_id, strategy_step_id);
CREATE INDEX IF NOT EXISTS idx_content_plan_rounds_line_version
    ON content_plan_rounds(production_line_version_id);

-- A production task created after this migration must have an immutable,
-- approved production-line version. Historical tasks are intentionally untouched.
CREATE TRIGGER IF NOT EXISTS trg_production_tasks_require_line_insert
BEFORE INSERT ON production_tasks
WHEN NEW.production_line_version_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'new production tasks require a production line version');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_tasks_require_line_provenance_update
BEFORE UPDATE OF round_id, master_plan_version_id, sub_plan_id,
                 sub_plan_version_id, strategy_step_id, production_line_version_id
ON production_tasks
WHEN NEW.production_line_version_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'new production task provenance requires a production line version');
END;
