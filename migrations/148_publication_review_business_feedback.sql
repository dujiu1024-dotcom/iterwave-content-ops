-- 148: publication evidence, independent review tasks, business-gap history and visual revision audit.
-- Existing drafts/metrics/visual versions remain authoritative; this migration adds traceable facts only.
CREATE TABLE IF NOT EXISTS closed_loop_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','delivered','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_closed_loop_events_delivery
    ON closed_loop_events(status, created_at);

CREATE TABLE IF NOT EXISTS publish_verifications (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('verified','legacy_unknown')),
    platform_content_id TEXT NOT NULL,
    platform_url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    content_snapshot_json TEXT NOT NULL CHECK(json_valid(content_snapshot_json)),
    verified_at TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publish_verifications_verified_at
    ON publish_verifications(verified_at DESC);

CREATE TABLE IF NOT EXISTS review_tasks (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(id) ON DELETE RESTRICT,
    publish_verification_id TEXT NOT NULL REFERENCES publish_verifications(id) ON DELETE RESTRICT,
    production_line_version_id INTEGER REFERENCES production_line_versions(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK(status IN ('scheduled','collecting','partial','completed','failed','cancelled')),
    collection_source TEXT NOT NULL DEFAULT 'manual'
        CHECK(collection_source IN ('manual','platform_api','platform_readback')),
    collect_after TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT,
    platform_content_id TEXT NOT NULL,
    publish_snapshot_json TEXT NOT NULL CHECK(json_valid(publish_snapshot_json)),
    collection_result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(collection_result_json)),
    conclusion_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(conclusion_json)),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_tasks_status_collect_after
    ON review_tasks(status, collect_after);
CREATE INDEX IF NOT EXISTS idx_review_tasks_line_version
    ON review_tasks(production_line_version_id, status);

CREATE TABLE IF NOT EXISTS review_task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_task_id TEXT NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json)),
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_task_events_task_time
    ON review_task_events(review_task_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS business_gap_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','shrinking','resolved','new')),
    severity TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN ('low','medium','high','critical')),
    description TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
    source_review_task_id TEXT REFERENCES review_tasks(id) ON DELETE SET NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_gap_history_key_time
    ON business_gap_snapshots(gap_key, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS visual_revision_audits (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    base_visual_version_id INTEGER REFERENCES visual_design_versions(id) ON DELETE SET NULL,
    generated_visual_version_id INTEGER REFERENCES visual_design_versions(id) ON DELETE SET NULL,
    scope TEXT NOT NULL CHECK(scope IN ('whole_set','single_page')),
    target_page_index INTEGER,
    original_reason TEXT NOT NULL CHECK(length(trim(original_reason)) > 0),
    normalized_instruction TEXT NOT NULL CHECK(length(trim(normalized_instruction)) > 0),
    final_prompt TEXT NOT NULL CHECK(length(trim(final_prompt)) > 0),
    style_reference_json TEXT NOT NULL CHECK(json_valid(style_reference_json)),
    preserved_page_hashes_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(preserved_page_hashes_json)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','generated','style_rejected','approved','failed')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    CHECK((scope='whole_set' AND target_page_index IS NULL)
       OR (scope='single_page' AND target_page_index IS NOT NULL AND target_page_index >= 0))
);
CREATE INDEX IF NOT EXISTS idx_visual_revision_audits_content_time
    ON visual_revision_audits(content_id, created_at DESC);
