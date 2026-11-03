-- 139: explicit production-line identity and immutable, subplan-bound versions.
-- A subplan owns exactly one logical production line; edits create new versions.
CREATE TABLE IF NOT EXISTS production_lines (
    id TEXT PRIMARY KEY,
    sub_plan_id TEXT NOT NULL UNIQUE
        REFERENCES sub_plans(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','active','paused','completed','cancelled','archived')),
    current_version_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL DEFAULT 'system',
    activated_at TEXT,
    activated_by TEXT,
    pause_reason TEXT,
    FOREIGN KEY(current_version_id) REFERENCES production_line_versions(id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS production_line_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    production_line_id TEXT NOT NULL
        REFERENCES production_lines(id) ON DELETE RESTRICT,
    sub_plan_version_id INTEGER NOT NULL
        REFERENCES sub_plan_versions(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','effective','rejected','archived')),
    definition_json TEXT NOT NULL,
    generation_source TEXT NOT NULL DEFAULT 'manual'
        CHECK (generation_source IN ('ai','manual','system')),
    based_on_version_id INTEGER
        REFERENCES production_line_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL DEFAULT 'user',
    reviewed_at TEXT,
    reviewed_by TEXT,
    change_note TEXT,
    UNIQUE(production_line_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_production_lines_status
    ON production_lines(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_production_line_versions_subplan
    ON production_line_versions(sub_plan_version_id, status);
CREATE INDEX IF NOT EXISTS idx_production_line_versions_line
    ON production_line_versions(production_line_id, version_no DESC);

-- Keep the exact production-line version on each round and task for traceability.
ALTER TABLE spiral_rounds ADD COLUMN production_line_version_id INTEGER
    REFERENCES production_line_versions(id) ON DELETE RESTRICT;
ALTER TABLE production_tasks ADD COLUMN production_line_version_id INTEGER
    REFERENCES production_line_versions(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_spiral_rounds_production_line
    ON spiral_rounds(production_line_version_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_production_tasks_line_version
    ON production_tasks(production_line_version_id, status);

CREATE INDEX IF NOT EXISTS idx_production_lines_current_version
    ON production_lines(current_version_id);
CREATE INDEX IF NOT EXISTS idx_production_line_versions_based_on
    ON production_line_versions(based_on_version_id);
