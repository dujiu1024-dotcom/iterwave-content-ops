-- B subplan production metadata for auditable round origin and applied review feedback.
-- Kept separate from A's frozen version-lifecycle migration.
ALTER TABLE spiral_rounds ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'manual'
    CHECK(trigger_source IN ('manual','auto','scheduler','api'));
ALTER TABLE spiral_rounds ADD COLUMN applied_feedback TEXT;

-- Stable stage identity across immutable subplan revisions. Existing historical stages
-- receive unique legacy keys; new revisions explicitly carry their predecessor's key.
ALTER TABLE sub_plan_stages ADD COLUMN stage_key TEXT;
UPDATE sub_plan_stages SET stage_key='legacy:' || id WHERE stage_key IS NULL;
CREATE INDEX IF NOT EXISTS idx_sub_plan_stages_stage_key
    ON sub_plan_stages(stage_key, sub_plan_version_id);

-- A stage's confirmed publish count follows its stable identity across versions,
-- while each version still gets its own stage row for immutable round attribution.
DROP VIEW IF EXISTS sub_plan_stage_publish_stats;
CREATE VIEW sub_plan_stage_publish_stats AS
SELECT
    current_stage.id AS stage_id,
    current_stage.sub_plan_version_id,
    COUNT(DISTINCT d.id) AS confirmed_published_count
FROM sub_plan_stages current_stage
LEFT JOIN sub_plan_stages historical_stage
    ON historical_stage.stage_key=current_stage.stage_key
LEFT JOIN spiral_rounds r
    ON r.sub_plan_stage_id=historical_stage.id
   AND r.sub_plan_version_id=historical_stage.sub_plan_version_id
LEFT JOIN contents c ON c.round_id=r.id
LEFT JOIN drafts d ON d.content_id=c.id
    AND d.user_confirmed_at IS NOT NULL
GROUP BY current_stage.id, current_stage.sub_plan_version_id;
