-- 170: remove Migration 150 constraints that contradict per-content task batches.
-- This is a forward-only repair: tenants that already applied migration 150 must
-- receive the same schema correction without rewriting historical migration checksums.
--
-- A strategy step may create multiple independent content tasks (task_ordinal > 1),
-- and each task owns its own production line. The line table's per-task uniqueness
-- and per-content uniqueness remain the correct integrity boundaries.
DROP TRIGGER IF EXISTS trg_production_tasks_single_item_ordinal;
DROP INDEX IF EXISTS idx_content_production_line_single_active;
