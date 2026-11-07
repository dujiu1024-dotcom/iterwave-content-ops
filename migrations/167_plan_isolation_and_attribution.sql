-- 167: 母计划隔离 + 内容/草稿计划归属 + 阶段独立指标
-- 1) 母计划版本快照只保留战略字段；平台执行参数拆到独立表
CREATE TABLE IF NOT EXISTS master_plan_runtime_configs (
    master_plan_version_id INTEGER PRIMARY KEY,
    cadence_json TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_plan_configs (
    master_plan_version_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    topic_count INTEGER CHECK (topic_count IS NULL OR topic_count > 0),
    publish_slots_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (master_plan_version_id, platform)
);

-- 2) 内容 / 草稿计划归属（NULL = 未归属，禁止伪造）
ALTER TABLE contents ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE contents ADD COLUMN sub_plan_id TEXT;
ALTER TABLE contents ADD COLUMN sub_plan_version_id INTEGER;
ALTER TABLE contents ADD COLUMN sub_plan_stage_id INTEGER;
ALTER TABLE contents ADD COLUMN production_line_version_id INTEGER;
ALTER TABLE contents ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'
    CHECK (lifecycle_status IN ('active','orphaned','published','discarded'));

ALTER TABLE drafts ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE drafts ADD COLUMN sub_plan_id TEXT;
ALTER TABLE drafts ADD COLUMN sub_plan_version_id INTEGER;
ALTER TABLE drafts ADD COLUMN sub_plan_stage_id INTEGER;

-- 3) 历史内容回填：先按生产任务回溯，阶段归属按轮次补齐
UPDATE contents SET
    master_plan_version_id = (
        SELECT t.master_plan_version_id FROM production_tasks t
        WHERE t.id = contents.production_task_id),
    sub_plan_id = (
        SELECT t.sub_plan_id FROM production_tasks t
        WHERE t.id = contents.production_task_id),
    sub_plan_version_id = (
        SELECT t.sub_plan_version_id FROM production_tasks t
        WHERE t.id = contents.production_task_id),
    production_line_version_id = (
        SELECT t.production_line_version_id FROM production_tasks t
        WHERE t.id = contents.production_task_id)
WHERE production_task_id IS NOT NULL;

UPDATE contents SET sub_plan_stage_id = (
    SELECT r.sub_plan_stage_id FROM spiral_rounds r WHERE r.id = contents.round_id)
WHERE sub_plan_stage_id IS NULL;

UPDATE drafts SET
    master_plan_version_id = (
        SELECT c.master_plan_version_id FROM contents c WHERE c.id = drafts.content_id),
    sub_plan_id = (
        SELECT c.sub_plan_id FROM contents c WHERE c.id = drafts.content_id),
    sub_plan_version_id = (
        SELECT c.sub_plan_version_id FROM contents c WHERE c.id = drafts.content_id),
    sub_plan_stage_id = (
        SELECT c.sub_plan_stage_id FROM contents c WHERE c.id = drafts.content_id)
WHERE EXISTS (SELECT 1 FROM contents c WHERE c.id = drafts.content_id);

-- 4) 阶段独立计划指标（NULL = 未配置/待采集，禁止 DEFAULT 0，见 R21）
ALTER TABLE sub_plan_stages ADD COLUMN target_metric TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN target_value REAL;
ALTER TABLE sub_plan_stages ADD COLUMN metric_scope TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN baseline_value REAL;
ALTER TABLE sub_plan_stages ADD COLUMN baseline_at TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN metric_period TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN data_source TEXT;

CREATE INDEX IF NOT EXISTS idx_contents_round_plan
    ON contents(round_id, sub_plan_stage_id);
CREATE INDEX IF NOT EXISTS idx_drafts_content_plan
    ON drafts(content_id, sub_plan_stage_id);
CREATE INDEX IF NOT EXISTS idx_platform_plan_configs_platform
    ON platform_plan_configs(platform);
