-- Record execution requests without retaining user input or running custom code.
CREATE TABLE IF NOT EXISTS tenant_skill_executions (
  execution_id TEXT PRIMARY KEY,
  skill_id TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('rejected','queued','running','succeeded','failed','timed_out','cancelled')),
  reason_code TEXT NOT NULL,
  input_bytes INTEGER NOT NULL DEFAULT 0,
  input_sha256 TEXT NOT NULL DEFAULT '',
  requested_at TEXT NOT NULL,
  finished_at TEXT,
  diagnostic_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_executions_skill_time
  ON tenant_skill_executions(skill_id, requested_at DESC);
