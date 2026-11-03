-- Persist migration file checksums so applied migrations cannot silently drift.
-- Historical migrations are baselined by the migration runner on first upgrade.
CREATE TABLE IF NOT EXISTS schema_migration_checksums (
    version INTEGER PRIMARY KEY
        REFERENCES schema_version(version) ON DELETE CASCADE,
    checksum TEXT NOT NULL,
    verification_status TEXT NOT NULL
        CHECK (verification_status IN ('baseline', 'applied', 'alias')),
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
