-- 161: retain immutable quality/commercial classifications alongside the constrained privacy enum.
-- The privacy enum remains in privacy_snapshot (migration 156); this JSON column stores the
-- remaining classification snapshot captured when the asset is attached to a production task.
ALTER TABLE production_task_asset_usages
    ADD COLUMN asset_quality_snapshot TEXT NOT NULL DEFAULT '{}';
