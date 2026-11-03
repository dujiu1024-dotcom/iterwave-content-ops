-- Keep uncertain platform-save state separate from the constrained drafts.status enum.
-- This avoids rewriting the existing drafts table while preserving manual reconciliation.
CREATE TABLE IF NOT EXISTS draft_save_verifications (
    draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending_verify'
        CHECK (status IN ('pending_verify', 'verified_saved', 'verified_missing')),
    attempted_at DATETIME NOT NULL,
    verified_at DATETIME,
    platform_draft_id TEXT,
    error_msg TEXT,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_draft_save_verifications_status
    ON draft_save_verifications(status, attempted_at);
