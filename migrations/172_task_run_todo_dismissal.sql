-- 172: auditable dismissal for terminal task-run todos without rewriting execution status.
ALTER TABLE task_runs ADD COLUMN todo_dismissed_at DATETIME;
ALTER TABLE task_runs ADD COLUMN todo_dismissed_by TEXT;
ALTER TABLE task_runs ADD COLUMN todo_dismissal_note TEXT;

CREATE INDEX IF NOT EXISTS idx_task_runs_unresolved_todos
    ON task_runs(status, COALESCE(finished_at, started_at, created_at) DESC, id DESC)
    WHERE resolved_at IS NULL AND todo_dismissed_at IS NULL
      AND status IN ('pending_verify','failed','interrupted');
