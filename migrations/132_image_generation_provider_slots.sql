-- Cross-process global concurrency slots for billable image-provider requests.
CREATE TABLE IF NOT EXISTS image_generation_provider_slots (
    slot_id INTEGER PRIMARY KEY CHECK (slot_id IN (1, 2)),
    lease_id TEXT UNIQUE,
    expires_at REAL,
    CHECK (
        (lease_id IS NULL AND expires_at IS NULL)
        OR (lease_id IS NOT NULL AND expires_at IS NOT NULL)
    )
);
INSERT OR IGNORE INTO image_generation_provider_slots (slot_id) VALUES (1);
INSERT OR IGNORE INTO image_generation_provider_slots (slot_id) VALUES (2);
CREATE INDEX IF NOT EXISTS idx_image_generation_provider_slots_expiry
    ON image_generation_provider_slots (expires_at);
