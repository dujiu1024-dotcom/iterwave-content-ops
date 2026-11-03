-- 144: unified original asset inventory without duplicating existing file storage.
-- Existing media_assets, content_assets and knowledge-base files remain the source of truth.
CREATE TABLE IF NOT EXISTS original_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type TEXT NOT NULL CHECK (asset_type IN (
        'product_image','product_description','product_manual',
        'technical_document','customer_case','customer_review','historical_article',
        'brand_material','credential','other'
    )),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL CHECK (source_kind IN ('media_asset','content_asset','kb_source','external','manual')),
    source_media_asset_id INTEGER REFERENCES media_assets(id) ON DELETE RESTRICT,
    source_content_asset_id INTEGER REFERENCES content_assets(id) ON DELETE RESTRICT,
    source_kb_id TEXT,
    source_uri TEXT,
    source_version TEXT,
    current_version_no INTEGER NOT NULL DEFAULT 1 CHECK (current_version_no > 0),
    readability_status TEXT NOT NULL DEFAULT 'unknown' CHECK (readability_status IN ('unknown','readable','unreadable','not_applicable')),
    completeness_status TEXT NOT NULL DEFAULT 'unknown' CHECK (completeness_status IN ('unknown','complete','incomplete')),
    duplicate_status TEXT NOT NULL DEFAULT 'unknown' CHECK (duplicate_status IN ('unknown','unique','duplicate','possible_duplicate')),
    freshness_status TEXT NOT NULL DEFAULT 'unknown' CHECK (freshness_status IN ('unknown','current','stale','expired')),
    description_status TEXT NOT NULL DEFAULT 'missing' CHECK (description_status IN ('missing','present')),
    commercial_use_status TEXT NOT NULL DEFAULT 'unknown' CHECK (commercial_use_status IN ('unknown','allowed','restricted','prohibited')),
    privacy_status TEXT NOT NULL DEFAULT 'unknown' CHECK (privacy_status IN ('unknown','clear','not_applicable','contains_personal_data','restricted')),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','confirmed','rejected')),
    review_note TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (source_kind = 'media_asset' AND source_media_asset_id IS NOT NULL
            AND source_content_asset_id IS NULL AND source_kb_id IS NULL)
        OR (source_kind = 'content_asset' AND source_content_asset_id IS NOT NULL
            AND source_media_asset_id IS NULL AND source_kb_id IS NULL)
        OR (source_kind = 'kb_source' AND source_kb_id IS NOT NULL
            AND source_media_asset_id IS NULL AND source_content_asset_id IS NULL)
        OR (source_kind IN ('external','manual')
            AND source_media_asset_id IS NULL AND source_content_asset_id IS NULL AND source_kb_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_original_assets_media_source
    ON original_assets(source_media_asset_id) WHERE source_media_asset_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_original_assets_content_source
    ON original_assets(source_content_asset_id) WHERE source_content_asset_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_original_assets_kb_source
    ON original_assets(source_kb_id) WHERE source_kb_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_original_assets_type_review
    ON original_assets(asset_type, review_status, archived_at);
CREATE INDEX IF NOT EXISTS idx_original_assets_quality
    ON original_assets(readability_status, completeness_status, duplicate_status, freshness_status);

CREATE TABLE IF NOT EXISTS original_asset_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_asset_id INTEGER NOT NULL REFERENCES original_assets(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    source_version TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL CHECK (review_status IN ('pending','confirmed','rejected')),
    commercial_use_status TEXT NOT NULL CHECK (commercial_use_status IN ('unknown','allowed','restricted','prohibited')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(original_asset_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_original_asset_versions_asset
    ON original_asset_versions(original_asset_id, version_no DESC);

CREATE TABLE IF NOT EXISTS original_asset_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_asset_id INTEGER NOT NULL REFERENCES original_assets(id) ON DELETE RESTRICT,
    entity_type TEXT NOT NULL CHECK (entity_type IN (
        'business','product','service','customer_case','content_topic','platform','plan'
    )),
    entity_id TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(original_asset_id, entity_type, entity_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_original_asset_links_entity
    ON original_asset_links(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_original_asset_links_asset
    ON original_asset_links(original_asset_id);

CREATE TABLE IF NOT EXISTS original_asset_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_asset_id INTEGER NOT NULL REFERENCES original_assets(id) ON DELETE RESTRICT,
    asset_version_no INTEGER NOT NULL CHECK (asset_version_no > 0),
    analysis_type TEXT NOT NULL CHECK (analysis_type IN (
        'image_product_extraction','document_value_extraction',
        'case_structuring','content_suitability','duplicate_conflict_detection','general'
    )),
    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','needs_review')),
    result_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    provider TEXT,
    model TEXT,
    error_code TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','confirmed','rejected')),
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(original_asset_id, asset_version_no)
        REFERENCES original_asset_versions(original_asset_id, version_no) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_original_asset_analyses_asset
    ON original_asset_analyses(original_asset_id, asset_version_no, created_at DESC);

CREATE TABLE IF NOT EXISTS original_asset_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('business','product','service','customer_case','plan','platform','content_topic')),
    entity_id TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK (asset_type IN (
        'product_image','product_description','product_manual',
        'technical_document','customer_case','customer_review','historical_article',
        'brand_material','credential','other'
    )),
    title TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','critical')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','fulfilled','waived')),
    source_plan_id TEXT,
    resolved_asset_id INTEGER REFERENCES original_assets(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, asset_type, title)
);
CREATE INDEX IF NOT EXISTS idx_original_asset_gaps_status
    ON original_asset_gaps(status, priority, entity_type, entity_id);

-- Immutable record of the exact confirmed asset version made available to a production task.
CREATE TABLE IF NOT EXISTS production_task_asset_usages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    production_task_id TEXT NOT NULL REFERENCES production_tasks(id) ON DELETE RESTRICT,
    original_asset_id INTEGER NOT NULL REFERENCES original_assets(id) ON DELETE RESTRICT,
    asset_version_no INTEGER NOT NULL CHECK (asset_version_no > 0),
    usage_role TEXT NOT NULL DEFAULT 'reference',
    permission_snapshot TEXT NOT NULL CHECK (permission_snapshot IN ('allowed','restricted')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(original_asset_id, asset_version_no)
        REFERENCES original_asset_versions(original_asset_id, version_no) ON DELETE RESTRICT,
    UNIQUE(production_task_id, original_asset_id, asset_version_no, usage_role)
);
CREATE INDEX IF NOT EXISTS idx_production_task_asset_usages_task
    ON production_task_asset_usages(production_task_id);
CREATE INDEX IF NOT EXISTS idx_production_task_asset_usages_asset
    ON production_task_asset_usages(original_asset_id, asset_version_no);

-- Historical asset versions and task usage snapshots are append-only.
CREATE TRIGGER IF NOT EXISTS trg_original_asset_versions_immutable_update
BEFORE UPDATE ON original_asset_versions
BEGIN
    SELECT RAISE(ABORT, 'original asset versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_original_asset_versions_immutable_delete
BEFORE DELETE ON original_asset_versions
BEGIN
    SELECT RAISE(ABORT, 'original asset versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_task_asset_usages_immutable_update
BEFORE UPDATE ON production_task_asset_usages
BEGIN
    SELECT RAISE(ABORT, 'production task asset usage snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_task_asset_usages_immutable_delete
BEFORE DELETE ON production_task_asset_usages
BEGIN
    SELECT RAISE(ABORT, 'production task asset usage snapshots are immutable');
END;
