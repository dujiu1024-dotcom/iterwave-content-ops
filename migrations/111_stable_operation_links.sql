-- Stable task/operation/notification references; nullable for legacy rows.
ALTER TABLE ops_logs ADD COLUMN task_run_id INTEGER REFERENCES task_runs(id);
ALTER TABLE notifications ADD COLUMN task_run_id INTEGER REFERENCES task_runs(id);
ALTER TABLE notifications ADD COLUMN ops_log_id INTEGER REFERENCES ops_logs(id);
ALTER TABLE notifications ADD COLUMN round_id INTEGER REFERENCES spiral_rounds(id);
ALTER TABLE notifications ADD COLUMN draft_id TEXT REFERENCES drafts(id);

CREATE INDEX IF NOT EXISTS idx_ops_logs_task_run_id ON ops_logs(task_run_id);
CREATE INDEX IF NOT EXISTS idx_notifications_task_run_id ON notifications(task_run_id);
CREATE INDEX IF NOT EXISTS idx_notifications_ops_log_id ON notifications(ops_log_id);
CREATE INDEX IF NOT EXISTS idx_notifications_round_id ON notifications(round_id);
CREATE INDEX IF NOT EXISTS idx_notifications_draft_id ON notifications(draft_id);
