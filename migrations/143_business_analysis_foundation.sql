-- 143: tenant-scoped business analysis foundation and immutable plan snapshots.
-- Each tenant has its own SQLite database; these tables are therefore tenant-scoped.
CREATE TABLE IF NOT EXISTS business_profiles (
    id INTEGER PRIMARY KEY CHECK(id=1),
    business_name TEXT NOT NULL DEFAULT '',
    introduction TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL DEFAULT '',
    business_model TEXT NOT NULL DEFAULT '',
    business_stage TEXT NOT NULL DEFAULT '',
    operating_goal TEXT NOT NULL DEFAULT '',
    field_evidence_json TEXT NOT NULL DEFAULT '{}',
    version_no INTEGER NOT NULL DEFAULT 1 CHECK(version_no >= 1),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS business_catalog_items (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('product','service','audience','market')),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 160),
    details_json TEXT NOT NULL DEFAULT '{}',
    field_evidence_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_business_catalog_type_active
    ON business_catalog_items(entity_type,is_active,updated_at DESC);

CREATE TABLE IF NOT EXISTS business_profile_versions (
    version_no INTEGER PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    change_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS master_plan_business_snapshots (
    master_plan_version_id INTEGER PRIMARY KEY
        REFERENCES master_plan_versions(id) ON DELETE RESTRICT,
    business_profile_version_no INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

-- A snapshot is captured at the exact transition to effective, not when a draft is created.
CREATE TRIGGER IF NOT EXISTS trg_capture_business_snapshot_on_master_activation
AFTER UPDATE OF status ON master_plan_versions
WHEN NEW.status='effective' AND OLD.status!='effective'
BEGIN
    INSERT OR IGNORE INTO master_plan_business_snapshots(
        master_plan_version_id,business_profile_version_no,snapshot_json,captured_at
    )
    SELECT NEW.id,
           COALESCE((SELECT version_no FROM business_profiles WHERE id=1),0),
           json_object(
             'profile', json(COALESCE((SELECT json_object(
                'business_name',business_name,'introduction',introduction,'industry',industry,
                'business_model',business_model,'business_stage',business_stage,
                'operating_goal',operating_goal,'field_evidence',json(field_evidence_json),
                'version_no',version_no
             ) FROM business_profiles WHERE id=1), json('{}'))),
             'items', json(COALESCE((SELECT json_group_array(json_object(
                'id',id,'entity_type',entity_type,'name',name,
                'details',json(details_json),'field_evidence',json(field_evidence_json),
                'is_active',is_active
             )) FROM business_catalog_items WHERE is_active=1), json('[]')))
           ),
           COALESCE(NEW.effective_at,NEW.reviewed_at,NEW.created_at,CURRENT_TIMESTAMP);
END;

-- Backfill a snapshot for already-effective plans so the API always has an explicit result.
INSERT OR IGNORE INTO master_plan_business_snapshots(
    master_plan_version_id,business_profile_version_no,snapshot_json,captured_at
)
SELECT id,COALESCE((SELECT version_no FROM business_profiles WHERE id=1),0),
       json_object(
         'profile',json(COALESCE((SELECT json_object(
           'business_name',business_name,'introduction',introduction,'industry',industry,
           'business_model',business_model,'business_stage',business_stage,
           'operating_goal',operating_goal,'field_evidence',json(field_evidence_json),
           'version_no',version_no
         ) FROM business_profiles WHERE id=1),json('{}'))),
         'items',json(COALESCE((SELECT json_group_array(json_object(
           'id',id,'entity_type',entity_type,'name',name,
           'details',json(details_json),'field_evidence',json(field_evidence_json),
           'is_active',is_active
         )) FROM business_catalog_items WHERE is_active=1),json('[]')))
       ),
       COALESCE(effective_at,reviewed_at,created_at,CURRENT_TIMESTAMP)
FROM master_plan_versions WHERE status='effective';
