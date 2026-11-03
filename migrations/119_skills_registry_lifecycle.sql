-- Extend tenant-scoped Skill manifests. Custom code remains non-executable.
ALTER TABLE tenant_skill_registry ADD COLUMN source TEXT NOT NULL DEFAULT 'user';
ALTER TABLE tenant_skill_registry ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tenant_skill_registry ADD COLUMN content_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE tenant_skill_registry ADD COLUMN validation_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tenant_skill_registry ADD COLUMN execution_state TEXT NOT NULL DEFAULT 'blocked_no_sandbox';
CREATE TABLE IF NOT EXISTS tenant_skill_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_id TEXT NOT NULL,
  version TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(skill_id, version),
  FOREIGN KEY(skill_id) REFERENCES tenant_skill_registry(skill_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tenant_skill_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_id TEXT NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_audit_skill_time ON tenant_skill_audit(skill_id, created_at DESC);
CREATE TABLE IF NOT EXISTS tenant_skill_references (
  skill_id TEXT NOT NULL,
  reference_type TEXT NOT NULL,
  reference_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(skill_id, reference_type, reference_id),
  FOREIGN KEY(skill_id) REFERENCES tenant_skill_registry(skill_id) ON DELETE CASCADE
);
