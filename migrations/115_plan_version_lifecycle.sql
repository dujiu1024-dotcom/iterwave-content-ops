-- 115: immutable master-plan versions, subplan snapshots/stages, and round attribution.
-- Existing content_plans remains the subplan/calendar aggregate; no parallel production tables.
-- All backfills are additive. Legacy rounds without evidence remain explicitly unattributed.

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

-- Restore every historical signature event, not merely unique signatures.
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

-- A plan_settings row may predate signature_history or have lost its matching event.
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

-- Only the newest matching historical event is the current effective version.
UPDATE master_plan_versions
SET status='effective',
    effective_at=COALESCE(effective_at,created_at)
WHERE id=(
    SELECT v.id
    FROM master_plan_versions v
    JOIN plan_settings ps ON ps.id=1 AND ps.plan_signature=v.plan_signature
    ORDER BY v.version_no DESC
    LIMIT 1
);

-- Keep one effective version if no plan_settings row exists but history does.
UPDATE master_plan_versions
SET status='effective', effective_at=COALESCE(effective_at,created_at)
WHERE id=(SELECT MAX(id) FROM master_plan_versions)
  AND NOT EXISTS (SELECT 1 FROM master_plan_versions WHERE status='effective');

ALTER TABLE plan_settings ADD COLUMN active_version_id INTEGER;
UPDATE plan_settings
SET active_version_id=(
    SELECT id FROM master_plan_versions
    WHERE status='effective'
    ORDER BY version_no DESC LIMIT 1
)
WHERE id=1;

ALTER TABLE spiral_rounds ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE spiral_rounds ADD COLUMN content_plan_version_id INTEGER;
ALTER TABLE spiral_rounds ADD COLUMN content_plan_stage_id INTEGER;
ALTER TABLE spiral_rounds ADD COLUMN plan_attribution_status TEXT NOT NULL DEFAULT 'pending'
    CHECK(plan_attribution_status IN ('pending','resolved','manual_review'));

-- First use the exact round-to-history event, then fall back to the exact signature.
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

-- Non-empty but unresolvable signatures are not silently guessed.
UPDATE spiral_rounds
SET plan_attribution_status='manual_review'
WHERE plan_signature IS NOT NULL AND master_plan_version_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_round_master_plan_version
    ON spiral_rounds(master_plan_version_id, created_at);
CREATE INDEX IF NOT EXISTS idx_round_subplan_version
    ON spiral_rounds(content_plan_version_id, content_plan_stage_id);
CREATE INDEX IF NOT EXISTS idx_round_plan_attribution
    ON spiral_rounds(plan_attribution_status, id);

-- Existing content_plans is retained and extended as the subplan/calendar aggregate.
ALTER TABLE content_plans ADD COLUMN platform TEXT;
ALTER TABLE content_plans ADD COLUMN master_plan_version_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_content_plans_master_version
    ON content_plans(master_plan_version_id, scheduled_date);

CREATE TABLE IF NOT EXISTS content_plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES content_plans(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    master_plan_version_id INTEGER REFERENCES master_plan_versions(id),
    snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'effective'
        CHECK(status IN ('draft','effective','archived')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    effective_at TEXT,
    change_note TEXT,
    UNIQUE(plan_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_content_plan_versions_plan
    ON content_plan_versions(plan_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_content_plan_versions_master
    ON content_plan_versions(master_plan_version_id);

-- Preserve the current legacy subplan state as its initial immutable snapshot.
INSERT OR IGNORE INTO content_plan_versions(
    plan_id, version_no, master_plan_version_id, snapshot_json,
    status, created_at, created_by, effective_at, change_note
)
SELECT
    cp.id, cp.version, cp.master_plan_version_id,
    json_object(
        'id',cp.id,'title',cp.title,'topic',cp.topic,
        'platforms',json(COALESCE(cp.platforms_json,'[]')),
        'platform',cp.platform,'planned_count',cp.planned_count,
        'scheduled_date',cp.scheduled_date,'status',cp.status,'notes',cp.notes,
        'version',cp.version
    ),
    CASE WHEN cp.status IN ('archived','cancelled') THEN 'archived' ELSE 'effective' END,
    COALESCE(cp.updated_at,cp.created_at,CURRENT_TIMESTAMP),
    'legacy_recovery',
    COALESCE(cp.updated_at,cp.created_at,CURRENT_TIMESTAMP),
    '由现有 content_plans 当前记录补建；旧版未保留的中间编辑历史不伪造'
FROM content_plans cp;

CREATE TABLE IF NOT EXISTS content_plan_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_plan_version_id INTEGER NOT NULL
        REFERENCES content_plan_versions(id) ON DELETE CASCADE,
    stage_order INTEGER NOT NULL CHECK(stage_order >= 1),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
    target_count INTEGER NOT NULL DEFAULT 1 CHECK(target_count BETWEEN 1 AND 10000),
    start_date TEXT,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','in_progress','completed','paused','cancelled')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(content_plan_version_id, stage_order),
    CHECK(start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_content_plan_stages_version
    ON content_plan_stages(content_plan_version_id, stage_order);
CREATE INDEX IF NOT EXISTS idx_content_plan_stages_status
    ON content_plan_stages(status, start_date, end_date);

-- Cross-version stage consistency is enforced by PlanVersionStore.bind_round()
-- and independently audited by PlanVersionStore.validate_integrity().

CREATE VIEW IF NOT EXISTS content_plan_stage_publish_stats AS
SELECT
    s.id AS stage_id,
    s.content_plan_version_id,
    COUNT(DISTINCT d.id) AS confirmed_published_count
FROM content_plan_stages s
LEFT JOIN spiral_rounds r
    ON r.content_plan_stage_id=s.id
   AND r.content_plan_version_id=s.content_plan_version_id
LEFT JOIN contents c ON c.round_id=r.id
LEFT JOIN drafts d ON d.content_id=c.id
    AND d.user_confirmed_at IS NOT NULL
GROUP BY s.id, s.content_plan_version_id;
