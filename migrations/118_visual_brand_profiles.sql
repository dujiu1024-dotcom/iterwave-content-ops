-- 118: tenant-scoped visual brand and style preferences.
CREATE TABLE IF NOT EXISTS visual_brand_profiles (
    tenant_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(profile_json)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
