-- B subplan production metadata for auditable round origin and applied review feedback.
-- Kept separate from A's frozen version-lifecycle migration.
ALTER TABLE spiral_rounds ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'manual'
    CHECK(trigger_source IN ('manual','auto','scheduler','api'));
ALTER TABLE spiral_rounds ADD COLUMN applied_feedback TEXT;
