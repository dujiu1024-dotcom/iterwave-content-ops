-- 146: protect immutable business-profile history and approved-plan snapshots.
-- Current profile/catalog rows remain editable; each save creates a new version.
CREATE TRIGGER IF NOT EXISTS trg_business_profile_versions_no_update
BEFORE UPDATE ON business_profile_versions
BEGIN
    SELECT RAISE(ABORT, 'business_profile_versions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_business_profile_versions_no_delete
BEFORE DELETE ON business_profile_versions
BEGIN
    SELECT RAISE(ABORT, 'business_profile_versions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_master_plan_business_snapshots_no_update
BEFORE UPDATE ON master_plan_business_snapshots
BEGIN
    SELECT RAISE(ABORT, 'master_plan_business_snapshots is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_master_plan_business_snapshots_no_delete
BEFORE DELETE ON master_plan_business_snapshots
BEGIN
    SELECT RAISE(ABORT, 'master_plan_business_snapshots is append-only');
END;
