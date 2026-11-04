-- 148: persist the exact privacy classification approved when an asset is attached to a production task.
-- Historical rows remain 'unknown'; their approval-time privacy state cannot be reconstructed safely.
ALTER TABLE production_task_asset_usages
    ADD COLUMN privacy_snapshot TEXT NOT NULL DEFAULT 'unknown'
    CHECK (privacy_snapshot IN (
        'unknown','clear','not_applicable','contains_personal_data','restricted'
    ));
