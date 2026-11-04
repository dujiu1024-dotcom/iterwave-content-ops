-- 149: version-bound business subgoals, dependency gates, evidence and production plans.
CREATE TABLE IF NOT EXISTS sub_goals (
    id TEXT PRIMARY KEY,
    sub_plan_version_id INTEGER NOT NULL REFERENCES sub_plan_versions(id) ON DELETE RESTRICT,
    stage_id INTEGER REFERENCES sub_plan_stages(id) ON DELETE RESTRICT,
    parent_goal_id TEXT REFERENCES sub_goals(id) ON DELETE RESTRICT,
    goal_type TEXT NOT NULL CHECK(goal_type IN ('quantity','business','effect','quality','capability')),
    goal_order INTEGER NOT NULL DEFAULT 1 CHECK(goal_order > 0),
    priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200),
    description TEXT NOT NULL DEFAULT '',
    target_count INTEGER CHECK(target_count IS NULL OR target_count >= 0),
    acceptance_criteria TEXT NOT NULL CHECK(length(trim(acceptance_criteria)) > 0),
    requires_production INTEGER NOT NULL DEFAULT 0 CHECK(requires_production IN (0,1)),
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK(status IN ('draft','blocked','ready','in_progress','completed','failed','needs_review','cancelled')),
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(sub_plan_version_id, goal_order)
);
CREATE INDEX IF NOT EXISTS idx_sub_goals_version_tree
    ON sub_goals(sub_plan_version_id,parent_goal_id,goal_order,id);
CREATE INDEX IF NOT EXISTS idx_sub_goals_stage_status
    ON sub_goals(sub_plan_version_id,stage_id,status);

CREATE TABLE IF NOT EXISTS sub_goal_dependencies (
    goal_id TEXT NOT NULL REFERENCES sub_goals(id) ON DELETE RESTRICT,
    depends_on_goal_id TEXT NOT NULL REFERENCES sub_goals(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(goal_id,depends_on_goal_id),
    CHECK(goal_id != depends_on_goal_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_goal_dependencies_predecessor
    ON sub_goal_dependencies(depends_on_goal_id,goal_id);

CREATE TABLE IF NOT EXISTS sub_goal_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT NOT NULL REFERENCES sub_goals(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK(decision IN ('passed','failed','needs_review')),
    reason TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL,
    actual_count INTEGER,
    reviewed_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_goal_evidence_goal
    ON sub_goal_evidence(goal_id,created_at DESC,id DESC);

CREATE TABLE IF NOT EXISTS sub_goal_production_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT NOT NULL UNIQUE REFERENCES sub_goals(id) ON DELETE RESTRICT,
    content_direction TEXT NOT NULL DEFAULT '',
    suggested_count INTEGER NOT NULL DEFAULT 1 CHECK(suggested_count BETWEEN 1 AND 10000),
    resource_requirements_json TEXT NOT NULL DEFAULT '{}',
    priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
    execution_conditions TEXT NOT NULL DEFAULT '',
    acceptance_relation TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_sub_goal_evidence_no_update
BEFORE UPDATE ON sub_goal_evidence BEGIN
    SELECT RAISE(ABORT, 'sub_goal_evidence is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_sub_goal_evidence_no_delete
BEFORE DELETE ON sub_goal_evidence BEGIN
    SELECT RAISE(ABORT, 'sub_goal_evidence is append-only');
END;

