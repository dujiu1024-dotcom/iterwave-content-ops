-- 166: persist the published work identity discovered by creator-center readback.
-- Keep the platform draft ID separate: it identifies the saved draft, not necessarily the published work.
ALTER TABLE drafts ADD COLUMN platform_work_id TEXT;
ALTER TABLE drafts ADD COLUMN platform_work_url TEXT;

CREATE INDEX IF NOT EXISTS idx_drafts_platform_work
    ON drafts(platform, platform_work_id)
    WHERE platform_work_id IS NOT NULL;
