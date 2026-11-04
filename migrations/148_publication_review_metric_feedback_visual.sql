-- 148: verified publication evidence, independent review tasks, unified metric facts,
-- operating-gap history, and auditable visual regeneration requests.
-- Additive only: legacy drafts/metrics/metric_snapshots and visual versions remain intact.

CREATE TABLE IF NOT EXISTS content_publish_verifications (
    verification_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(id) ON DELETE RESTRICT,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE RESTRICT,
    production_task_id TEXT,
    production_line_id TEXT,
    platform TEXT NOT NULL CHECK(platform IN ('xhs','douyin','kuaishou','wechat_channels')),
    platform_content_id TEXT NOT NULL CHECK(length(trim(platform_content_id)) BETWEEN 1 AND 200),
    content_url TEXT NOT NULL CHECK(length(trim(content_url)) BETWEEN 8 AND 2048),
    published_at TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    content_snapshot_json TEXT NOT NULL CHECK(json_valid(content_snapshot_json)),
    content_snapshot_sha256 TEXT NOT NULL CHECK(length(content_snapshot_sha256)=64),
    evidence_source TEXT NOT NULL DEFAULT 'user_confirmed',
    evidence_note TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL UNIQUE,
    closure_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(closure_status IN ('pending','processing','succeeded','failed','not_required')),
    closure_error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publish_verifications_tenant_time
    ON content_publish_verifications(tenant_id, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_publish_verifications_content
    ON content_publish_verifications(content_id, verified_at DESC);

CREATE TABLE IF NOT EXISTS production_line_close_outbox (
    event_id TEXT PRIMARY KEY,
    verification_id TEXT NOT NULL UNIQUE
        REFERENCES content_publish_verifications(verification_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK(event_type='content.publish_verified.v1'),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','processing','delivered','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    claimed_by TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publish_close_outbox_status
    ON production_line_close_outbox(status, created_at);

CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    verification_id TEXT NOT NULL UNIQUE
        REFERENCES content_publish_verifications(verification_id) ON DELETE RESTRICT,
    tenant_id TEXT NOT NULL,
    draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE RESTRICT,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL CHECK(platform IN ('xhs','douyin','kuaishou','wechat_channels')),
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK(status IN ('scheduled','pending','running','partial','failed','completed','cancelled')),
    scheduled_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts >= 1),
    collection_cycle_id TEXT NOT NULL DEFAULT 'initial',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_window_start TEXT,
    last_window_end TEXT,
    last_error TEXT,
    conclusion_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(conclusion_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_review_tasks_tenant_status
    ON review_tasks(tenant_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_review_tasks_content
    ON review_tasks(content_id, created_at DESC);

CREATE TABLE IF NOT EXISTS review_collection_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE RESTRICT,
    source TEXT NOT NULL CHECK(source IN ('platform_readback','manual','import')),
    status TEXT NOT NULL CHECK(status IN ('succeeded','partial','failed')),
    window_start TEXT,
    window_end TEXT,
    timezone_name TEXT NOT NULL DEFAULT 'UTC',
    metrics_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metrics_json)),
    error_message TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_collection_task_time
    ON review_collection_runs(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS metric_facts (
    fact_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    draft_id TEXT,
    content_id TEXT NOT NULL,
    platform TEXT NOT NULL CHECK(platform IN ('xhs','douyin','kuaishou','wechat_channels')),
    metric_key TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL CHECK(unit IN ('count','ratio')),
    source TEXT NOT NULL CHECK(source IN ('manual','platform_readback','import')),
    source_record_type TEXT NOT NULL CHECK(source_record_type IN ('metric_snapshot','metrics','review_collection_run')),
    source_record_id TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    timezone_name TEXT NOT NULL DEFAULT 'UTC',
    collection_status TEXT NOT NULL CHECK(collection_status IN ('succeeded','partial')),
    correction_of TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_record_type, source_record_id, metric_key)
);
CREATE INDEX IF NOT EXISTS idx_metric_facts_tenant_metric_time
    ON metric_facts(tenant_id, metric_key, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_metric_facts_content_time
    ON metric_facts(tenant_id, content_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_metric_facts_source_time
    ON metric_facts(tenant_id, source, recorded_at DESC);

CREATE TABLE IF NOT EXISTS metric_fact_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('insert','update','delete')),
    before_json TEXT,
    after_json TEXT,
    audited_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metric_fact_audit_fact
    ON metric_fact_audit(fact_id, audited_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_metric_fact_audit_insert
AFTER INSERT ON metric_facts
BEGIN
    INSERT INTO metric_fact_audit(fact_id,action,after_json,audited_at)
    VALUES(NEW.fact_id,'insert',json_object(
        'tenant_id',NEW.tenant_id,'content_id',NEW.content_id,'metric_key',NEW.metric_key,
        'value',NEW.value,'unit',NEW.unit,'source',NEW.source,'measured_at',NEW.measured_at,
        'window_start',NEW.window_start,'window_end',NEW.window_end,
        'timezone_name',NEW.timezone_name,'collection_status',NEW.collection_status
    ),CURRENT_TIMESTAMP);
END;
CREATE TRIGGER IF NOT EXISTS trg_metric_fact_audit_update
AFTER UPDATE ON metric_facts
BEGIN
    INSERT INTO metric_fact_audit(fact_id,action,before_json,after_json,audited_at)
    VALUES(NEW.fact_id,'update',
        json_object('value',OLD.value,'source',OLD.source,'measured_at',OLD.measured_at,
                    'window_start',OLD.window_start,'window_end',OLD.window_end,
                    'collection_status',OLD.collection_status),
        json_object('value',NEW.value,'source',NEW.source,'measured_at',NEW.measured_at,
                    'window_start',NEW.window_start,'window_end',NEW.window_end,
                    'collection_status',NEW.collection_status),CURRENT_TIMESTAMP);
END;
CREATE TRIGGER IF NOT EXISTS trg_metric_fact_audit_delete
AFTER DELETE ON metric_facts
BEGIN
    INSERT INTO metric_fact_audit(fact_id,action,before_json,audited_at)
    VALUES(OLD.fact_id,'delete',json_object('value',OLD.value,'metric_key',OLD.metric_key,
        'source',OLD.source,'measured_at',OLD.measured_at),CURRENT_TIMESTAMP);
END;

-- Existing manually entered/imported snapshots become normalized metric facts.
INSERT OR IGNORE INTO metric_facts(
    fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
    source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
    collection_status,correction_of,created_at
)
SELECT lower(hex(randomblob(16))),ms.tenant_id,ms.draft_id,ms.content_id,ms.platform,
       metric.metric_key,metric.metric_value,'count',ms.source,'metric_snapshot',ms.id,
       ms.measured_at,ms.recorded_at,'UTC','succeeded',ms.correction_of,ms.created_at
FROM metric_snapshots ms
JOIN (
    SELECT 'views' AS metric_key UNION ALL SELECT 'likes' UNION ALL SELECT 'collects'
    UNION ALL SELECT 'comments' UNION ALL SELECT 'shares'
) names
LEFT JOIN (
    SELECT id, 'views' AS metric_key, views AS metric_value FROM metric_snapshots WHERE views IS NOT NULL
    UNION ALL SELECT id, 'likes', likes FROM metric_snapshots WHERE likes IS NOT NULL
    UNION ALL SELECT id, 'collects', collects FROM metric_snapshots WHERE collects IS NOT NULL
    UNION ALL SELECT id, 'comments', comments FROM metric_snapshots WHERE comments IS NOT NULL
    UNION ALL SELECT id, 'shares', shares FROM metric_snapshots WHERE shares IS NOT NULL
) metric ON metric.id=ms.id AND metric.metric_key=names.metric_key
WHERE metric.metric_value IS NOT NULL;

-- Existing successful/partial platform readbacks become normalized facts. NULL remains unknown;
-- an explicit numeric 0 remains a real zero-valued observation.
INSERT OR IGNORE INTO metric_facts(
    fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
    source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
    collection_status,created_at
)
SELECT lower(hex(randomblob(16))),'legacy',m.draft_id,m.content_id,m.platform,
       metric.metric_key,metric.metric_value,metric.unit,'platform_readback','metrics',
       CAST(m.id AS TEXT),m.fetched_at,m.fetched_at,'UTC',CASE WHEN m.fetch_status='success' THEN 'succeeded' ELSE 'partial' END,CURRENT_TIMESTAMP
FROM metrics m
JOIN (
    SELECT 'views' AS metric_key,'count' AS unit UNION ALL SELECT 'likes','count'
    UNION ALL SELECT 'collects','count' UNION ALL SELECT 'comments','count'
    UNION ALL SELECT 'shares','count' UNION ALL SELECT 'new_followers','count'
    UNION ALL SELECT 'like_rate','ratio' UNION ALL SELECT 'collect_rate','ratio'
    UNION ALL SELECT 'interaction_rate','ratio' UNION ALL SELECT 'follower_efficiency','ratio'
) names
LEFT JOIN (
    SELECT id,'views' AS metric_key,views AS metric_value FROM metrics WHERE views IS NOT NULL
    UNION ALL SELECT id,'likes',likes FROM metrics WHERE likes IS NOT NULL
    UNION ALL SELECT id,'collects',collects FROM metrics WHERE collects IS NOT NULL
    UNION ALL SELECT id,'comments',comments FROM metrics WHERE comments IS NOT NULL
    UNION ALL SELECT id,'shares',shares FROM metrics WHERE shares IS NOT NULL
    UNION ALL SELECT id,'new_followers',new_followers FROM metrics WHERE new_followers IS NOT NULL
    UNION ALL SELECT id,'like_rate',like_rate FROM metrics WHERE like_rate IS NOT NULL
    UNION ALL SELECT id,'collect_rate',collect_rate FROM metrics WHERE collect_rate IS NOT NULL
    UNION ALL SELECT id,'interaction_rate',interaction_rate FROM metrics WHERE interaction_rate IS NOT NULL
    UNION ALL SELECT id,'follower_efficiency',follower_efficiency FROM metrics WHERE follower_efficiency IS NOT NULL
) metric ON metric.id=m.id AND metric.metric_key=names.metric_key
WHERE m.fetch_status IN ('success','partial') AND m.fetched_at IS NOT NULL
  AND metric.metric_value IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS trg_metric_snapshot_to_facts_insert
AFTER INSERT ON metric_snapshots
BEGIN
    INSERT INTO metric_facts(
        fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
        source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
        collection_status,correction_of,created_at
    )
    SELECT lower(hex(randomblob(16))),NEW.tenant_id,NEW.draft_id,NEW.content_id,NEW.platform,
           metric.metric_key,metric.metric_value,'count',NEW.source,'metric_snapshot',NEW.id,
           NEW.measured_at,NEW.recorded_at,'UTC','succeeded',NEW.correction_of,NEW.created_at
    FROM (
        SELECT 'views' AS metric_key,NEW.views AS metric_value UNION ALL
        SELECT 'likes',NEW.likes UNION ALL SELECT 'collects',NEW.collects
        UNION ALL SELECT 'comments',NEW.comments UNION ALL SELECT 'shares',NEW.shares
    ) metric
    WHERE metric.metric_value IS NOT NULL
    ON CONFLICT(source_record_type,source_record_id,metric_key) DO UPDATE SET
        value=excluded.value,source=excluded.source,measured_at=excluded.measured_at,
        recorded_at=excluded.recorded_at,correction_of=excluded.correction_of,
        collection_status=excluded.collection_status;
END;

CREATE TRIGGER IF NOT EXISTS trg_metrics_to_facts_insert
AFTER INSERT ON metrics
WHEN NEW.fetch_status IN ('success','partial') AND NEW.fetched_at IS NOT NULL
BEGIN
    INSERT INTO metric_facts(
        fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
        source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
        collection_status,created_at
    )
    SELECT lower(hex(randomblob(16))),'legacy',NEW.draft_id,NEW.content_id,NEW.platform,
           metric.metric_key,metric.metric_value,metric.unit,'platform_readback','metrics',
           CAST(NEW.id AS TEXT),NEW.fetched_at,NEW.fetched_at,'UTC',CASE WHEN NEW.fetch_status='success' THEN 'succeeded' ELSE 'partial' END,CURRENT_TIMESTAMP
    FROM (
        SELECT 'views' AS metric_key,NEW.views AS metric_value,'count' AS unit UNION ALL
        SELECT 'likes',NEW.likes,'count' UNION ALL SELECT 'collects',NEW.collects,'count'
        UNION ALL SELECT 'comments',NEW.comments,'count' UNION ALL SELECT 'shares',NEW.shares,'count'
        UNION ALL SELECT 'new_followers',NEW.new_followers,'count'
        UNION ALL SELECT 'like_rate',NEW.like_rate,'ratio' UNION ALL SELECT 'collect_rate',NEW.collect_rate,'ratio'
        UNION ALL SELECT 'interaction_rate',NEW.interaction_rate,'ratio'
        UNION ALL SELECT 'follower_efficiency',NEW.follower_efficiency,'ratio'
    ) metric
    WHERE metric.metric_value IS NOT NULL
    ON CONFLICT(source_record_type,source_record_id,metric_key) DO UPDATE SET
        value=excluded.value,measured_at=excluded.measured_at,recorded_at=excluded.recorded_at,
        collection_status=excluded.collection_status;
END;

CREATE TRIGGER IF NOT EXISTS trg_metrics_to_facts_update
AFTER UPDATE OF fetch_status,fetched_at,views,likes,collects,comments,shares,new_followers,
                like_rate,collect_rate,interaction_rate,follower_efficiency ON metrics
WHEN NEW.fetch_status IN ('success','partial') AND NEW.fetched_at IS NOT NULL
BEGIN
    DELETE FROM metric_facts
    WHERE source_record_type='metrics' AND source_record_id=CAST(NEW.id AS TEXT);
    INSERT INTO metric_facts(
        fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
        source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
        collection_status,created_at
    )
    SELECT lower(hex(randomblob(16))),'legacy',NEW.draft_id,NEW.content_id,NEW.platform,
           metric.metric_key,metric.metric_value,metric.unit,'platform_readback','metrics',
           CAST(NEW.id AS TEXT),NEW.fetched_at,NEW.fetched_at,'UTC',CASE WHEN NEW.fetch_status='success' THEN 'succeeded' ELSE 'partial' END,CURRENT_TIMESTAMP
    FROM (
        SELECT 'views' AS metric_key,NEW.views AS metric_value,'count' AS unit UNION ALL
        SELECT 'likes',NEW.likes,'count' UNION ALL SELECT 'collects',NEW.collects,'count'
        UNION ALL SELECT 'comments',NEW.comments,'count' UNION ALL SELECT 'shares',NEW.shares,'count'
        UNION ALL SELECT 'new_followers',NEW.new_followers,'count'
        UNION ALL SELECT 'like_rate',NEW.like_rate,'ratio' UNION ALL SELECT 'collect_rate',NEW.collect_rate,'ratio'
        UNION ALL SELECT 'interaction_rate',NEW.interaction_rate,'ratio'
        UNION ALL SELECT 'follower_efficiency',NEW.follower_efficiency,'ratio'
    ) metric
    WHERE metric.metric_value IS NOT NULL
    ON CONFLICT(source_record_type,source_record_id,metric_key) DO UPDATE SET
        value=excluded.value,measured_at=excluded.measured_at,recorded_at=excluded.recorded_at,
        collection_status=excluded.collection_status;
END;

CREATE VIEW IF NOT EXISTS metric_facts_effective AS
SELECT f.* FROM metric_facts f
WHERE NOT EXISTS (
    SELECT 1 FROM metric_facts correction
    WHERE f.source_record_type='metric_snapshot'
      AND correction.source_record_type='metric_snapshot'
      AND correction.tenant_id=f.tenant_id
      AND correction.content_id=f.content_id
      AND correction.correction_of=f.source_record_id
      AND correction.metric_key=f.metric_key
);

CREATE TABLE IF NOT EXISTS business_gaps (
    gap_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    gap_key TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
    description TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'medium'
        CHECK(severity IN ('low','medium','high','critical')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','resolved')),
    source_review_task_id TEXT REFERENCES review_tasks(task_id) ON DELETE SET NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,gap_key)
);
CREATE INDEX IF NOT EXISTS idx_business_gaps_tenant_status
    ON business_gaps(tenant_id,status,severity,updated_at DESC);

CREATE TABLE IF NOT EXISTS business_gap_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id TEXT NOT NULL REFERENCES business_gaps(gap_id) ON DELETE RESTRICT,
    review_task_id TEXT REFERENCES review_tasks(task_id) ON DELETE SET NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('opened','updated','resolved','reopened')),
    reason TEXT NOT NULL DEFAULT '',
    before_json TEXT,
    after_json TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_gap_events_gap_time
    ON business_gap_events(gap_id,created_at DESC,event_id DESC);

CREATE TABLE IF NOT EXISTS visual_regeneration_requests (
    request_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE RESTRICT,
    scope TEXT NOT NULL CHECK(scope IN ('full','page')),
    page_index INTEGER,
    original_reason TEXT NOT NULL CHECK(length(trim(original_reason)) BETWEEN 1 AND 1000),
    structured_instruction TEXT NOT NULL,
    preserve_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(preserve_json)),
    forbidden_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(forbidden_json)),
    style_baseline_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(style_baseline_json)),
    reference_images_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(reference_images_json)),
    final_prompt_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(final_prompt_json)),
    source_version_id INTEGER REFERENCES visual_design_versions(id) ON DELETE RESTRICT,
    output_version_id INTEGER REFERENCES visual_design_versions(id) ON DELETE RESTRICT,
    visual_job_id TEXT REFERENCES visual_jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','succeeded','failed','rejected')),
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK((scope='full' AND page_index IS NULL) OR (scope='page' AND page_index >= 0))
);
CREATE INDEX IF NOT EXISTS idx_visual_regeneration_content_time
    ON visual_regeneration_requests(content_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_visual_regeneration_status
    ON visual_regeneration_requests(status,created_at DESC);


-- Latest observation per tenant/content/metric; consumers can still apply their own
-- date-window filter before ranking when calculating period-specific goals.
CREATE VIEW IF NOT EXISTS metric_facts_latest AS
SELECT ranked.* FROM (
    SELECT f.*,
           ROW_NUMBER() OVER (
               PARTITION BY f.tenant_id,f.content_id,f.metric_key
               ORDER BY f.measured_at DESC,f.recorded_at DESC,f.fact_id DESC
           ) AS observation_rank
    FROM metric_facts_effective f
) ranked
WHERE ranked.observation_rank=1;

ALTER TABLE visual_design_versions ADD COLUMN approved_at TEXT;

UPDATE visual_design_versions
SET status='superseded'
WHERE status='approved'
  AND id NOT IN (
      SELECT MAX(id) FROM visual_design_versions
      WHERE status='approved' GROUP BY content_id
  );
CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_one_approved_version_per_content
    ON visual_design_versions(content_id) WHERE status='approved';
