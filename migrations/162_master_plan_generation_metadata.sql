-- Persist AI-generated master-plan evidence as first-class version data.
-- Legacy rows remain readable from change_note; only new/explicitly re-saved metadata
-- is covered by the extended version signature.
ALTER TABLE master_plan_versions ADD COLUMN generation_metadata_json TEXT;
