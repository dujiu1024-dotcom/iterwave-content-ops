-- Append-only evidence when an operator confirms a platform-side orphan draft exists.
-- Source content remains missing and is never fabricated by this confirmation.
CREATE TABLE IF NOT EXISTS orphan_draft_platform_confirmation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    source_content_id TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('xhs','douyin','kuaishou','wechat_channels')),
    previous_status TEXT NOT NULL,
    previous_verification_status TEXT,
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    platform_saved_confirmed INTEGER NOT NULL CHECK (platform_saved_confirmed = 1),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orphan_platform_confirmation_draft
    ON orphan_draft_platform_confirmation_audit(draft_id, created_at);
