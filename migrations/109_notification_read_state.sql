-- 109 · 通知已读状态
-- 保留历史通知，read_at 为 NULL 表示未读；重复标记已读保持首次时间。
ALTER TABLE notifications ADD COLUMN read_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_notifications_read_sent
    ON notifications(read_at, sent_at DESC, id DESC);
