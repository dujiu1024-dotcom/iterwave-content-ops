-- 130: durable AI image-generation lifecycle, page checkpoints and account-group leases.
CREATE TABLE IF NOT EXISTS visual_generation_tasks (
    task_id TEXT PRIMARY KEY,
    account_group TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('pending','running','interrupted','retrying','completed','failed','cancelled')),
    owner TEXT,
    lease_expires_at REAL,
    started_at REAL,
    heartbeat_at REAL,
    finished_at REAL,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visual_tasks_group_status
    ON visual_generation_tasks(account_group, status);

CREATE TABLE IF NOT EXISTS visual_generation_task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES visual_generation_tasks(task_id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS visual_generation_pages (
    task_id TEXT NOT NULL REFERENCES visual_generation_tasks(task_id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL CHECK(page_index >= 0),
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
    output_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    error TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY(task_id, page_index)
);

CREATE TABLE IF NOT EXISTS visual_generation_account_leases (
    account_group TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES visual_generation_tasks(task_id) ON DELETE CASCADE,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
