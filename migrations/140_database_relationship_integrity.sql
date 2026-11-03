-- 140: enforce cross-row provenance and artifact identity at the database boundary.
-- These guards complement application validation and protect direct SQL/import paths.

CREATE TRIGGER IF NOT EXISTS trg_production_tasks_validate_insert
BEFORE INSERT ON production_tasks
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM spiral_rounds r
        JOIN sub_plans p ON p.id = r.sub_plan_id
        JOIN sub_plan_stages s
          ON s.id = r.sub_plan_stage_id
         AND s.sub_plan_version_id = r.sub_plan_version_id
        WHERE r.id = NEW.round_id
          AND r.master_plan_version_id = NEW.master_plan_version_id
          AND r.sub_plan_id = NEW.sub_plan_id
          AND r.sub_plan_version_id = NEW.sub_plan_version_id
          AND s.stage_key = NEW.strategy_step_id
          AND p.platform = NEW.platform
          AND r.production_line_version_id IS NEW.production_line_version_id
    ) THEN RAISE(ABORT, 'production task provenance does not match its round') END;
    SELECT CASE WHEN NEW.production_line_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM production_line_versions v
        JOIN production_lines l ON l.id = v.production_line_id
        JOIN sub_plan_versions sv ON sv.id = v.sub_plan_version_id
        WHERE v.id = NEW.production_line_version_id
          AND l.sub_plan_id = NEW.sub_plan_id
          AND sv.sub_plan_id = NEW.sub_plan_id
          AND v.sub_plan_version_id = NEW.sub_plan_version_id
    ) THEN RAISE(ABORT, 'production task line version does not match its subplan') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_tasks_validate_update
BEFORE UPDATE OF round_id, master_plan_version_id, sub_plan_id, sub_plan_version_id,
                 strategy_step_id, platform, production_line_version_id, content_id
ON production_tasks
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM spiral_rounds r
        JOIN sub_plans p ON p.id = r.sub_plan_id
        JOIN sub_plan_stages s
          ON s.id = r.sub_plan_stage_id
         AND s.sub_plan_version_id = r.sub_plan_version_id
        WHERE r.id = NEW.round_id
          AND r.master_plan_version_id = NEW.master_plan_version_id
          AND r.sub_plan_id = NEW.sub_plan_id
          AND r.sub_plan_version_id = NEW.sub_plan_version_id
          AND s.stage_key = NEW.strategy_step_id
          AND p.platform = NEW.platform
          AND r.production_line_version_id IS NEW.production_line_version_id
    ) THEN RAISE(ABORT, 'production task provenance does not match its round') END;
    SELECT CASE WHEN NEW.production_line_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM production_line_versions v
        JOIN production_lines l ON l.id = v.production_line_id
        JOIN sub_plan_versions sv ON sv.id = v.sub_plan_version_id
        WHERE v.id = NEW.production_line_version_id
          AND l.sub_plan_id = NEW.sub_plan_id
          AND sv.sub_plan_id = NEW.sub_plan_id
          AND v.sub_plan_version_id = NEW.sub_plan_version_id
    ) THEN RAISE(ABORT, 'production task line version does not match its subplan') END;
    SELECT CASE WHEN NEW.content_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM contents c
        WHERE c.id = NEW.content_id
          AND c.round_id = NEW.round_id
          AND c.platform = NEW.platform
          AND (c.production_task_id IS NULL OR c.production_task_id=NEW.id)
    ) THEN RAISE(ABORT, 'production task content identity mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_lines_validate_current_version_insert
BEFORE INSERT ON production_lines
WHEN NEW.current_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_line_versions v
        JOIN sub_plan_versions sv ON sv.id = v.sub_plan_version_id
        WHERE v.id = NEW.current_version_id
          AND v.production_line_id = NEW.id
          AND sv.sub_plan_id = NEW.sub_plan_id
    ) THEN RAISE(ABORT, 'current production line version belongs to another line') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_lines_validate_current_version_update
BEFORE UPDATE OF current_version_id, sub_plan_id ON production_lines
WHEN NEW.current_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_line_versions v
        JOIN sub_plan_versions sv ON sv.id = v.sub_plan_version_id
        WHERE v.id = NEW.current_version_id
          AND v.production_line_id = NEW.id
          AND sv.sub_plan_id = NEW.sub_plan_id
    ) THEN RAISE(ABORT, 'current production line version belongs to another line') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_line_versions_validate_insert
BEFORE INSERT ON production_line_versions
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_lines l
        JOIN sub_plan_versions sv ON sv.id = NEW.sub_plan_version_id
        WHERE l.id = NEW.production_line_id
          AND l.sub_plan_id = sv.sub_plan_id
    ) THEN RAISE(ABORT, 'production line version subplan mismatch') END;
    SELECT CASE WHEN NEW.based_on_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM production_line_versions parent
        WHERE parent.id = NEW.based_on_version_id
          AND parent.production_line_id = NEW.production_line_id
    ) THEN RAISE(ABORT, 'base production line version belongs to another line') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_line_versions_validate_update
BEFORE UPDATE OF production_line_id, sub_plan_version_id, based_on_version_id
ON production_line_versions
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_lines l
        JOIN sub_plan_versions sv ON sv.id = NEW.sub_plan_version_id
        WHERE l.id = NEW.production_line_id
          AND l.sub_plan_id = sv.sub_plan_id
    ) THEN RAISE(ABORT, 'production line version subplan mismatch') END;
    SELECT CASE WHEN NEW.based_on_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM production_line_versions parent
        WHERE parent.id = NEW.based_on_version_id
          AND parent.production_line_id = NEW.production_line_id
    ) THEN RAISE(ABORT, 'base production line version belongs to another line') END;
END;

-- Keep both sides of the task/artifact identity synchronized. Conditional
-- updates prevent recursive-trigger loops when recursive_triggers is enabled.
CREATE TRIGGER IF NOT EXISTS trg_contents_task_link_validate_insert
BEFORE INSERT ON contents
WHEN NEW.production_task_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_tasks t
        WHERE t.id = NEW.production_task_id
          AND t.round_id = NEW.round_id
          AND t.platform = NEW.platform
          AND (t.content_id IS NULL OR t.content_id = NEW.id)
    ) THEN RAISE(ABORT, 'content production task link mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_contents_task_link_validate_update
BEFORE UPDATE OF production_task_id, round_id, platform ON contents
WHEN NEW.production_task_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM production_tasks t
        WHERE t.id = NEW.production_task_id
          AND t.round_id = NEW.round_id
          AND t.platform = NEW.platform
          AND (t.content_id IS NULL OR t.content_id = NEW.id)
    ) THEN RAISE(ABORT, 'content production task link mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_tasks_sync_content_insert
AFTER INSERT ON production_tasks
WHEN NEW.content_id IS NOT NULL
BEGIN
    UPDATE contents SET production_task_id = NEW.id
    WHERE id = NEW.content_id AND production_task_id IS NOT NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_production_tasks_sync_content_update
AFTER UPDATE OF content_id ON production_tasks
WHEN NEW.content_id IS NOT NULL
BEGIN
    UPDATE contents SET production_task_id = NEW.id
    WHERE id = NEW.content_id AND production_task_id IS NOT NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_contents_sync_production_task_insert
AFTER INSERT ON contents
WHEN NEW.production_task_id IS NOT NULL
BEGIN
    UPDATE production_tasks SET content_id = NEW.id
    WHERE id = NEW.production_task_id AND content_id IS NOT NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_contents_sync_production_task_update
AFTER UPDATE OF production_task_id ON contents
WHEN NEW.production_task_id IS NOT NULL
BEGIN
    UPDATE production_tasks SET content_id = NEW.id
    WHERE id = NEW.production_task_id AND content_id IS NOT NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_contents_prevent_linked_delete
BEFORE DELETE ON contents
WHEN EXISTS (SELECT 1 FROM production_tasks WHERE content_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete content linked to a production task');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_tasks_prevent_linked_delete
BEFORE DELETE ON production_tasks
WHEN EXISTS (SELECT 1 FROM contents WHERE production_task_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete production task linked to content');
END;

-- Artifact identity is immutable once either side has been linked.
CREATE TRIGGER IF NOT EXISTS trg_production_tasks_prevent_content_relink
BEFORE UPDATE OF content_id ON production_tasks
WHEN OLD.content_id IS NOT NULL AND NEW.content_id IS NOT OLD.content_id
BEGIN
    SELECT RAISE(ABORT, 'production task content identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_contents_prevent_task_relink
BEFORE UPDATE OF production_task_id ON contents
WHEN OLD.production_task_id IS NOT NULL
 AND NEW.production_task_id IS NOT OLD.production_task_id
BEGIN
    SELECT RAISE(ABORT, 'content production task identity is immutable');
END;

-- The task table predates these relationships and cannot gain inline foreign keys
-- without a risky table rebuild. Restrict parent deletion/key mutation instead.
CREATE TRIGGER IF NOT EXISTS trg_rounds_prevent_task_orphan
BEFORE DELETE ON spiral_rounds
WHEN EXISTS (SELECT 1 FROM production_tasks WHERE round_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete a round referenced by production tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_master_plan_versions_prevent_task_orphan
BEFORE DELETE ON master_plan_versions
WHEN EXISTS (SELECT 1 FROM production_tasks WHERE master_plan_version_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete a master plan version referenced by production tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_sub_plans_prevent_task_orphan
BEFORE DELETE ON sub_plans
WHEN EXISTS (SELECT 1 FROM production_tasks WHERE sub_plan_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete a subplan referenced by production tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_sub_plan_versions_prevent_task_orphan
BEFORE DELETE ON sub_plan_versions
WHEN EXISTS (SELECT 1 FROM production_tasks WHERE sub_plan_version_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete a subplan version referenced by production tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_sub_plan_stages_prevent_task_orphan
BEFORE DELETE ON sub_plan_stages
WHEN EXISTS (
    SELECT 1 FROM production_tasks t
    WHERE t.sub_plan_version_id = OLD.sub_plan_version_id
      AND t.strategy_step_id = OLD.stage_key
)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete a strategy stage referenced by production tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_sub_plan_stages_prevent_task_rekey
BEFORE UPDATE OF stage_key, sub_plan_version_id ON sub_plan_stages
WHEN EXISTS (
    SELECT 1 FROM production_tasks t
    WHERE t.sub_plan_version_id = OLD.sub_plan_version_id
      AND t.strategy_step_id = OLD.stage_key
)
BEGIN
    SELECT RAISE(ABORT, 'cannot change the identity of a strategy stage referenced by production tasks');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_line_versions_prevent_task_orphan
BEFORE DELETE ON production_line_versions
WHEN EXISTS (
    SELECT 1 FROM production_tasks WHERE production_line_version_id = OLD.id
)
BEGIN
    SELECT RAISE(ABORT, 'cannot delete a production line version referenced by tasks');
END;
