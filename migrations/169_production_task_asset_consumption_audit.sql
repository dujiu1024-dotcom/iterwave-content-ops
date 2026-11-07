-- 169: append provider-accepted visual asset consumption evidence to the existing
-- immutable task-asset usage ledger. Authorization snapshots remain unchanged.
ALTER TABLE production_task_asset_usages ADD COLUMN provider TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN model TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN source_sha256 TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN result_sha256 TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN generation_event_id TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN consumed_at TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN page_role TEXT;
ALTER TABLE production_task_asset_usages ADD COLUMN page_index INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS idx_production_task_asset_usage_generation_event
    ON production_task_asset_usages(generation_event_id)
    WHERE generation_event_id IS NOT NULL;
