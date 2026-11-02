-- 115: immutable master/sub-plan versions, stages, round attribution and durable opening locks.
-- Legacy content_plans remains a calendar/content-plan aggregate and is not repurposed.

CREATE TABLE IF NOT EXISTS master_plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_no INTEGER NOT NULL UNIQUE,
    plan_signature TEXT NOT NULL,
    plan_snapshot TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'archived'
        CHECK(status IN ('draft','effective','archived')),
    source TEXT NOT NULL DEFAULT 'user'
        CHECK(source IN ('user','review_adoption','legacy_recovery','system')),
    legacy_history_id INTEGER UNIQUE REFERENCES signature_history(id),
    based_on_version_id INTEGER REFERENCES master_plan_versions(id),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    reviewed_at TEXT,
    reviewed_by TEXT,
    effective_at TEXT,
    superseded_at TEXT,
    change_note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_master_plan_one_effective
    ON master_plan_versions(status) WHERE status='effective';
CREATE INDEX IF NOT EXISTS idx_master_plan_signature
    ON master_plan_versions(plan_signature, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_master_plan_created
    ON master_plan_versions(created_at DESC, version_no DESC);

-- Restore each signature_history event independently; same signatures may represent
-- distinct historical events and must not be deduplicated.
INSERT OR IGNORE INTO master_plan_versions(
    version_no, plan_signature, plan_snapshot, status, source,
    legacy_history_id, created_at, created_by, change_note
)
SELECT h.id, h.plan_signature, h.plan_snapshot, 'archived', 'legacy_recovery',
       h.id, COALESCE(h.created_at, CURRENT_TIMESTAMP),
       COALESCE(NULLIF(h.author,''),'legacy'),
       '由 signature_history 自动恢复；保留原始历史事件'
FROM signature_history h
WHERE h.plan_signature IS NOT NULL AND h.plan_snapshot IS NOT NULL
ORDER BY h.id;

-- A plan_settings row may predate signature_history or have no matching event.
INSERT INTO master_plan_versions(
    version_no, plan_signature, plan_snapshot, status, source,
    created_at, created_by, change_note
)
SELECT
    COALESCE((SELECT MAX(version_no) FROM master_plan_versions),0)+1,
    ps.plan_signature, ps.plan_snapshot, 'effective', 'legacy_recovery',
    COALESCE(ps.updated_at,CURRENT_TIMESTAMP),
    COALESCE(NULLIF(ps.updated_by,''),'legacy'),
    '由 plan_settings 当前快照补建'
FROM plan_settings ps
WHERE ps.id=1
  AND NOT EXISTS (
      SELECT 1 FROM master_plan_versions v
      WHERE v.plan_signature=ps.plan_signature
        AND v.plan_snapshot=ps.plan_snapshot
  );

-- Recompute the one current version from the exact legacy settings snapshot.
UPDATE master_plan_versions SET status='archived';
UPDATE master_plan_versions
SET status='effective', effective_at=COALESCE(effective_at,created_at)
WHERE id=(
    SELECT v.id FROM master_plan_versions v
    JOIN plan_settings ps
      ON ps.id=1
     AND ps.plan_signature=v.plan_signature
     AND ps.plan_snapshot=v.plan_snapshot
    ORDER BY v.version_no DESC LIMIT 1
);

UPDATE master_plan_versions
SET status='effective', effective_at=COALESCE(effective_at,created_at)
WHERE id=(SELECT MAX(id) FROM master_plan_versions)
  AND NOT EXISTS (SELECT 1 FROM master_plan_versions WHERE status='effective');

ALTER TABLE plan_settings ADD COLUMN active_version_id INTEGER;
UPDATE plan_settings
SET active_version_id=(
    SELECT id FROM master_plan_versions
    WHERE status='effective' ORDER BY version_no DESC LIMIT 1
)
WHERE id=1;

ALTER TABLE spiral_rounds ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE spiral_rounds ADD COLUMN sub_plan_id TEXT;
ALTER TABLE spiral_rounds ADD COLUMN sub_plan_version_id INTEGER;
ALTER TABLE spiral_rounds ADD COLUMN sub_plan_stage_id INTEGER;
ALTER TABLE spiral_rounds ADD COLUMN sub_plan_snapshot TEXT;
ALTER TABLE spiral_rounds ADD COLUMN sub_plan_stage_snapshot TEXT;
ALTER TABLE spiral_rounds ADD COLUMN plan_attribution_status TEXT NOT NULL DEFAULT 'pending'
    CHECK(plan_attribution_status IN ('pending','resolved','manual_review'));

-- Exact round-history event takes precedence over signature-only fallback.
UPDATE spiral_rounds
SET master_plan_version_id=(
        SELECT v.id
        FROM signature_history h
        JOIN master_plan_versions v ON v.legacy_history_id=h.id
        WHERE h.round_id=spiral_rounds.id
          AND h.plan_signature=spiral_rounds.plan_signature
        ORDER BY h.id DESC LIMIT 1
    ),
    plan_attribution_status='resolved'
WHERE plan_signature IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM signature_history h
      WHERE h.round_id=spiral_rounds.id
        AND h.plan_signature=spiral_rounds.plan_signature
  );

UPDATE spiral_rounds
SET master_plan_version_id=(
        SELECT id FROM master_plan_versions
        WHERE plan_signature=spiral_rounds.plan_signature
        ORDER BY version_no DESC LIMIT 1
    ),
    plan_attribution_status='resolved'
WHERE plan_signature IS NOT NULL
  AND master_plan_version_id IS NULL
  AND EXISTS (
      SELECT 1 FROM master_plan_versions v
      WHERE v.plan_signature=spiral_rounds.plan_signature
  );

-- A non-empty signature with no matching snapshot needs human attribution.
UPDATE spiral_rounds
SET plan_attribution_status='manual_review'
WHERE plan_signature IS NOT NULL AND master_plan_version_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_round_master_plan_version
    ON spiral_rounds(master_plan_version_id, created_at);
CREATE INDEX IF NOT EXISTS idx_round_sub_plan_version
    ON spiral_rounds(sub_plan_id, sub_plan_version_id, sub_plan_stage_id);
CREATE INDEX IF NOT EXISTS idx_round_plan_attribution
    ON spiral_rounds(plan_attribution_status, id);

-- New first-class platform subplans; legacy content_plans remains untouched.
CREATE TABLE IF NOT EXISTS sub_plans (
    id TEXT PRIMARY KEY,
    master_plan_version_id INTEGER NOT NULL
        REFERENCES master_plan_versions(id),
    platform TEXT NOT NULL
        CHECK(platform IN ('xhs','douyin','kuaishou','wechat_channels')),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','active','paused','completed','archived','cancelled')),
    current_version_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'user',
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_plans_master_platform
    ON sub_plans(master_plan_version_id, platform, status);
CREATE INDEX IF NOT EXISTS idx_sub_plans_status
    ON sub_plans(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS sub_plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sub_plan_id TEXT NOT NULL REFERENCES sub_plans(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL,
    master_plan_version_id INTEGER NOT NULL
        REFERENCES master_plan_versions(id),
    snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','effective','archived','rejected')),
    based_on_version_id INTEGER REFERENCES sub_plan_versions(id),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'user',
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    effective_at TEXT,
    superseded_at TEXT,
    change_note TEXT,
    UNIQUE(sub_plan_id, version_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_plan_one_effective_version
    ON sub_plan_versions(sub_plan_id) WHERE status='effective';
CREATE INDEX IF NOT EXISTS idx_sub_plan_versions_master
    ON sub_plan_versions(master_plan_version_id, status);
CREATE INDEX IF NOT EXISTS idx_sub_plan_versions_created
    ON sub_plan_versions(sub_plan_id, version_no DESC);

CREATE TABLE IF NOT EXISTS sub_plan_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sub_plan_version_id INTEGER NOT NULL
        REFERENCES sub_plan_versions(id) ON DELETE RESTRICT,
    stage_order INTEGER NOT NULL CHECK(stage_order >= 1),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
    direction TEXT NOT NULL DEFAULT '',
    target_count INTEGER NOT NULL DEFAULT 1 CHECK(target_count BETWEEN 1 AND 10000),
    start_date TEXT,
    end_date TEXT,
    completion_condition TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','in_progress','completed','paused','cancelled')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(sub_plan_version_id, stage_order),
    CHECK(start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_sub_plan_stages_version
    ON sub_plan_stages(sub_plan_version_id, stage_order);
CREATE INDEX IF NOT EXISTS idx_sub_plan_stages_status
    ON sub_plan_stages(status, start_date, end_date);

-- Durable cross-process idempotency for scheduler/automatic round opening.
CREATE TABLE IF NOT EXISTS round_open_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    master_plan_version_id INTEGER NOT NULL REFERENCES master_plan_versions(id),
    sub_plan_id TEXT NOT NULL REFERENCES sub_plans(id),
    sub_plan_version_id INTEGER NOT NULL REFERENCES sub_plan_versions(id),
    sub_plan_stage_id INTEGER NOT NULL REFERENCES sub_plan_stages(id),
    status TEXT NOT NULL DEFAULT 'reserved'
        CHECK(status IN ('reserved','created','failed')),
    round_id INTEGER UNIQUE,
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_round_open_idempotency_status
    ON round_open_idempotency(status, lease_until);
CREATE INDEX IF NOT EXISTS idx_round_open_idempotency_stage
    ON round_open_idempotency(sub_plan_id, sub_plan_version_id, sub_plan_stage_id);

-- Count only explicit user-confirmed publish events, never generated/saved drafts.
CREATE VIEW IF NOT EXISTS sub_plan_stage_publish_stats AS
SELECT
    s.id AS stage_id,
    s.sub_plan_version_id,
    COUNT(DISTINCT d.id) AS confirmed_published_count
FROM sub_plan_stages s
LEFT JOIN spiral_rounds r
    ON r.sub_plan_stage_id=s.id
   AND r.sub_plan_version_id=s.sub_plan_version_id
LEFT JOIN contents c ON c.round_id=r.id
LEFT JOIN drafts d ON d.content_id=c.id
    AND d.user_confirmed_at IS NOT NULL
GROUP BY s.id, s.sub_plan_version_id;
