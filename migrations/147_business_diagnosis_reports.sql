-- 147: versioned AI business diagnosis reports with source snapshots and confirmation gates.
CREATE TABLE IF NOT EXISTS business_diagnosis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_no INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','confirmed','superseded','stale')),
    report_json TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'ai',
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT,
    change_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_business_diagnosis_status_version
    ON business_diagnosis_reports(status, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_business_diagnosis_fingerprint
    ON business_diagnosis_reports(source_fingerprint, status);
