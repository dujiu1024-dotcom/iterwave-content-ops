-- 117: immutable visual design versions and per-page visual jobs.
-- Content ownership/tenant scope is inherited through contents; do not duplicate account identity.
CREATE TABLE IF NOT EXISTS visual_design_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version > 0),
    content_hash TEXT NOT NULL,
    renderer TEXT NOT NULL CHECK(renderer IN ('chromium_v2','legacy_v1')),
    style_key TEXT NOT NULL,
    design_json TEXT NOT NULL CHECK(json_valid(design_json)),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','rendered','review_required','approved','superseded','failed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    UNIQUE(content_id, version)
);
CREATE INDEX IF NOT EXISTS idx_visual_design_versions_content
    ON visual_design_versions(content_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_visual_design_versions_status
    ON visual_design_versions(status, created_at DESC);

CREATE TABLE IF NOT EXISTS visual_jobs (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    visual_version_id INTEGER REFERENCES visual_design_versions(id) ON DELETE SET NULL,
    page_index INTEGER NOT NULL CHECK(page_index >= 0),
    job_type TEXT NOT NULL CHECK(job_type IN ('generate','revise','rerender','review')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json)),
    output_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(output_json)),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_visual_jobs_content
    ON visual_jobs(content_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_visual_jobs_queue
    ON visual_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_visual_jobs_version_page
    ON visual_jobs(visual_version_id, page_index, created_at DESC);
