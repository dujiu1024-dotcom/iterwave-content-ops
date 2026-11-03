-- Add indexes for foreign-key child columns that were previously unindexed
-- or only covered by composite indexes with a different leading column.
-- SQLite does not automatically index REFERENCES columns.
CREATE INDEX IF NOT EXISTS idx_ops_logs_round_id
    ON ops_logs(round_id);
CREATE INDEX IF NOT EXISTS idx_contents_topic_id
    ON contents(topic_id);
CREATE INDEX IF NOT EXISTS idx_signature_history_round_id
    ON signature_history(round_id);
CREATE INDEX IF NOT EXISTS idx_autogrow_runs_round_id
    ON autogrow_runs(round_id);
CREATE INDEX IF NOT EXISTS idx_content_events_sample_run
    ON content_events(sample_run_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_events_sample_content
    ON content_events(sample_content_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_metric_snapshots_draft_time
    ON metric_snapshots(draft_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_metric_snapshots_content_id_time
    ON metric_snapshots(content_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_metric_snapshots_correction_of
    ON metric_snapshots(correction_of);
CREATE INDEX IF NOT EXISTS idx_media_generation_jobs_asset
    ON media_generation_jobs(output_asset_id);
CREATE INDEX IF NOT EXISTS idx_master_plan_based_on_version
    ON master_plan_versions(based_on_version_id);
CREATE INDEX IF NOT EXISTS idx_sub_plan_versions_based_on_version
    ON sub_plan_versions(based_on_version_id);
CREATE INDEX IF NOT EXISTS idx_round_open_master_version
    ON round_open_idempotency(master_plan_version_id);
CREATE INDEX IF NOT EXISTS idx_round_open_sub_plan_version
    ON round_open_idempotency(sub_plan_version_id);
CREATE INDEX IF NOT EXISTS idx_round_open_sub_plan_stage
    ON round_open_idempotency(sub_plan_stage_id);
CREATE INDEX IF NOT EXISTS idx_user_override_round_id
    ON user_override(round_id);
CREATE INDEX IF NOT EXISTS idx_user_override_ai_call_id
    ON user_override(ai_call_id);
