-- Persist billable image-provider request and failure counts per manual generation job.
ALTER TABLE media_generation_jobs
    ADD COLUMN api_calls INTEGER NOT NULL DEFAULT 0 CHECK (api_calls >= 0);
ALTER TABLE media_generation_jobs
    ADD COLUMN failed_api_calls INTEGER NOT NULL DEFAULT 0 CHECK (failed_api_calls >= 0);
