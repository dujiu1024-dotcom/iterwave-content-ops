-- 133: durable content review decisions, explicit publication outcome, and strategy feedback.
-- Kept separate from the historical media-budget migration now assigned to version 134.
-- Review approval, external publication, and strategy target achievement are separate facts.

ALTER TABLE drafts ADD COLUMN publish_status TEXT NOT NULL DEFAULT 'not_published'
    CHECK(publish_status IN ('not_published','published','failed'));
ALTER TABLE drafts ADD COLUMN published_at TEXT;
ALTER TABLE drafts ADD COLUMN publish_result_note TEXT;
ALTER TABLE drafts ADD COLUMN publish_time_source TEXT NOT NULL DEFAULT 'confirmation_time'
    CHECK(publish_time_source IN ('confirmation_time','user_supplied'));
ALTER TABLE drafts ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK(review_status IN ('pending','approved','rejected'));

-- Restore existing review state from the canonical legacy rejection flag and latest approval audit.
UPDATE drafts SET review_status='rejected'
WHERE content_id IN (SELECT id FROM contents WHERE COALESCE(review_rejected,0)=1);
UPDATE drafts SET review_status='approved'
WHERE content_id IN (
    SELECT c.id FROM contents c
    WHERE COALESCE(c.review_rejected,0)=0
      AND (SELECT u.override_field FROM user_override u
           WHERE u.round_id=c.round_id
             AND u.override_field IN ('review_approve','review_reject','review_resubmit')
             AND CASE WHEN json_valid(u.original_value)
                      THEN json_extract(u.original_value,'$.content_id') ELSE NULL END=c.id
           ORDER BY u.overridden_at DESC,u.rowid DESC LIMIT 1)='review_approve'
);
CREATE INDEX IF NOT EXISTS idx_drafts_review_status
    ON drafts(review_status,created_at DESC);

-- Only an explicit historical confirmation proves publication. Lifecycle labels alone are not proof.
-- The timestamp is marked as confirmation time, not claimed to be user-entered publish time.
UPDATE drafts
SET publish_status='published',
    published_at=COALESCE(published_at, user_confirmed_at),
    publish_time_source='confirmation_time'
WHERE user_confirmed_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drafts_publish_status
    ON drafts(publish_status, published_at DESC);

CREATE TABLE IF NOT EXISTS content_review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE RESTRICT,
    draft_id TEXT REFERENCES drafts(id) ON DELETE SET NULL,
    round_id INTEGER REFERENCES spiral_rounds(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL CHECK(event_type IN
        ('edited','approved','rejected','resubmitted')),
    actor TEXT NOT NULL DEFAULT 'web-ui',
    reason TEXT NOT NULL DEFAULT '',
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_review_events_content
    ON content_review_events(content_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_content_review_events_round
    ON content_review_events(round_id, created_at DESC);

CREATE TABLE IF NOT EXISTS strategy_effect_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL REFERENCES sub_plan_stages(id) ON DELETE RESTRICT,
    assessment TEXT NOT NULL CHECK(assessment IN ('effective','mixed','ineffective','insufficient_data')),
    recommendation TEXT NOT NULL CHECK(length(trim(recommendation)) BETWEEN 1 AND 2000),
    evidence_note TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'low'
        CHECK(confidence IN ('low','medium','high')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT 'web-ui',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_effect_reviews_stage
    ON strategy_effect_reviews(stage_id, created_at DESC, id DESC);
