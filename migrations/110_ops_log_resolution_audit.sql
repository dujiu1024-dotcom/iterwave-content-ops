-- 110 — 为运营日志人工核实增加独立审计字段。
-- 不通过平台维度批量修改日志；每条日志必须由操作员单独核实。
ALTER TABLE ops_logs ADD COLUMN resolved_at DATETIME;
ALTER TABLE ops_logs ADD COLUMN resolved_by TEXT;
ALTER TABLE ops_logs ADD COLUMN resolution_action TEXT
    CHECK (resolution_action IS NULL OR resolution_action IN ('sent', 'not_sent'));
ALTER TABLE ops_logs ADD COLUMN resolution_note TEXT;
ALTER TABLE task_runs ADD COLUMN resolved_at DATETIME;
ALTER TABLE task_runs ADD COLUMN resolved_by TEXT;
ALTER TABLE task_runs ADD COLUMN resolution_action TEXT
    CHECK (resolution_action IS NULL OR resolution_action IN ('sent', 'not_sent'));
