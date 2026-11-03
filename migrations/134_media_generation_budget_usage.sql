-- 134: persist billable image-provider request and failure counts per manual generation job.
-- Version 131 is already used by master-plan lifecycle restoration.
-- The migration runner skips ADD COLUMN statements when a compatible column already exists,
-- preserving safety for databases where this change was previously applied under version 131.
ALTER TABLE media_generation_jobs
    ADD COLUMN api_calls INTEGER NOT NULL DEFAULT 0 CHECK (api_calls >= 0);
ALTER TABLE media_generation_jobs
    ADD COLUMN failed_api_calls INTEGER NOT NULL DEFAULT 0 CHECK (failed_api_calls >= 0);
