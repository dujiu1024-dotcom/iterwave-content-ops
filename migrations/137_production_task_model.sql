-- Durable identity and lifecycle for individual content-production work items.
-- Strategy-step identity is a stable sub_plan_stages.stage_key validated against
-- the immutable sub-plan version bound to each production round.
CREATE TABLE IF NOT EXISTS production_tasks (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    round_id INTEGER NOT NULL,
    master_plan_version_id INTEGER NOT NULL,
    sub_plan_id TEXT NOT NULL,
    sub_plan_version_id INTEGER NOT NULL,
    strategy_step_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    task_ordinal INTEGER NOT NULL DEFAULT 1 CHECK (task_ordinal > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    content_id TEXT,
    input_snapshot_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    available_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status != 'succeeded' OR content_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_production_tasks_round_status
    ON production_tasks(round_id, status);
CREATE INDEX IF NOT EXISTS idx_production_tasks_step_status
    ON production_tasks(strategy_step_id, status);
CREATE INDEX IF NOT EXISTS idx_production_tasks_recovery
    ON production_tasks(status, lease_expires_at, available_at);

CREATE TABLE IF NOT EXISTS production_task_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','cancelled','abandoned')),
    worker_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    error_message TEXT,
    output_content_id TEXT,
    UNIQUE(task_id, attempt_no),
    FOREIGN KEY(task_id) REFERENCES production_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_production_task_attempts_task
    ON production_task_attempts(task_id, attempt_no);

-- Preserve the task-to-artifact identity on the content row as well.
ALTER TABLE contents ADD COLUMN production_task_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_contents_production_task
    ON contents(production_task_id) WHERE production_task_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_production_tasks_content
    ON production_tasks(content_id) WHERE content_id IS NOT NULL;
