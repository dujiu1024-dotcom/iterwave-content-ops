-- 168: 阶段指标关联目标 + 计划归属回填补全（第二轮审查 F3/F6）
-- 1) 阶段指标显式关联 goals.id（goals.id 为 TEXT，如 "goal-xxx"），
--    避免复用 metric_period 承载目标 ID
ALTER TABLE sub_plan_stages ADD COLUMN goal_id TEXT;

-- 2) 轮次已绑定母计划但内容仍缺归属的，按轮次补齐（只填 NULL，不覆盖已有值）
UPDATE contents SET
    master_plan_version_id = COALESCE(contents.master_plan_version_id,
        (SELECT r.master_plan_version_id FROM spiral_rounds r WHERE r.id = contents.round_id)),
    sub_plan_id = COALESCE(contents.sub_plan_id,
        (SELECT r.sub_plan_id FROM spiral_rounds r WHERE r.id = contents.round_id)),
    sub_plan_version_id = COALESCE(contents.sub_plan_version_id,
        (SELECT r.sub_plan_version_id FROM spiral_rounds r WHERE r.id = contents.round_id)),
    sub_plan_stage_id = COALESCE(contents.sub_plan_stage_id,
        (SELECT r.sub_plan_stage_id FROM spiral_rounds r WHERE r.id = contents.round_id)),
    production_line_version_id = COALESCE(contents.production_line_version_id,
        (SELECT r.production_line_version_id FROM spiral_rounds r WHERE r.id = contents.round_id))
WHERE contents.master_plan_version_id IS NULL
  AND contents.round_id IN (
      SELECT id FROM spiral_rounds WHERE master_plan_version_id IS NOT NULL);

-- 3) 草稿归属从所属内容补齐
UPDATE drafts SET
    master_plan_version_id = COALESCE(drafts.master_plan_version_id,
        (SELECT c.master_plan_version_id FROM contents c WHERE c.id = drafts.content_id)),
    sub_plan_id = COALESCE(drafts.sub_plan_id,
        (SELECT c.sub_plan_id FROM contents c WHERE c.id = drafts.content_id)),
    sub_plan_version_id = COALESCE(drafts.sub_plan_version_id,
        (SELECT c.sub_plan_version_id FROM contents c WHERE c.id = drafts.content_id)),
    sub_plan_stage_id = COALESCE(drafts.sub_plan_stage_id,
        (SELECT c.sub_plan_stage_id FROM contents c WHERE c.id = drafts.content_id))
WHERE drafts.master_plan_version_id IS NULL
  AND drafts.content_id IN (
      SELECT id FROM contents WHERE master_plan_version_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_sub_plan_stages_goal
    ON sub_plan_stages(goal_id);
