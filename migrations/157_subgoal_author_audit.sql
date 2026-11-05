-- 157: retain the authenticated principal responsible for subgoal authoring and production-plan edits.
-- Historical rows remain explicitly unattributed; do not infer a human actor retroactively.
ALTER TABLE sub_goals ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE sub_goals ADD COLUMN updated_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE sub_goal_production_plans ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy:unattributed';
ALTER TABLE sub_goal_production_plans ADD COLUMN updated_by TEXT NOT NULL DEFAULT 'legacy:unattributed';

CREATE INDEX IF NOT EXISTS idx_sub_goals_created_by
    ON sub_goals(sub_plan_version_id,created_by,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_goal_plans_updated_by
    ON sub_goal_production_plans(updated_by,updated_at DESC);
