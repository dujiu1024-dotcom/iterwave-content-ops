-- 136: strategy-oriented platform subplan steps and immutable execution outcomes.
ALTER TABLE sub_plan_stages ADD COLUMN strategy_goal TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN current_problem TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN execution_strategy TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN prerequisites TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN input_info TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN expected_result TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN completion_criteria TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN effect_evaluation TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN entry_condition TEXT NOT NULL DEFAULT '';
ALTER TABLE sub_plan_stages ADD COLUMN entry_condition_met INTEGER NOT NULL DEFAULT 1
    CHECK(entry_condition_met IN (0,1));
ALTER TABLE sub_plan_stages ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '[]';

-- Preserve the legacy completion condition as the new structured criterion.
UPDATE sub_plan_stages
SET completion_criteria=completion_condition
WHERE completion_criteria='' AND completion_condition!='';

CREATE TABLE IF NOT EXISTS sub_plan_stage_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sub_plan_id TEXT NOT NULL REFERENCES sub_plans(id) ON DELETE RESTRICT,
    stage_key TEXT NOT NULL,
    source_version_id INTEGER NOT NULL REFERENCES sub_plan_versions(id) ON DELETE RESTRICT,
    stage_id INTEGER NOT NULL REFERENCES sub_plan_stages(id) ON DELETE RESTRICT,
    result_json TEXT NOT NULL,
    evaluation TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL DEFAULT 'user'
);
CREATE INDEX IF NOT EXISTS idx_sub_plan_stage_outcomes_lookup
    ON sub_plan_stage_outcomes(sub_plan_id, stage_key, recorded_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sub_plan_stage_outcomes_version
    ON sub_plan_stage_outcomes(source_version_id, stage_id);
