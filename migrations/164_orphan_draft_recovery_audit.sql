-- Append-only evidence for an operator-confirmed orphan draft relink.
-- Deliberately no FK to drafts: the recovery evidence must survive later retention.
CREATE TABLE IF NOT EXISTS orphan_draft_recovery_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    source_content_id TEXT NOT NULL,
    target_content_id TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('xhs','douyin','kuaishou','wechat_channels')),
    previous_status TEXT NOT NULL,
    previous_verification_status TEXT,
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    platform_missing_confirmed INTEGER NOT NULL CHECK (platform_missing_confirmed = 1),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_orphan_draft_recovery_audit_draft
    ON orphan_draft_recovery_audit(draft_id, created_at);
