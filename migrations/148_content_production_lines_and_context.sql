-- 148: one immutable production line per individual content task.
-- Existing subplan production_lines remain as legacy strategy templates; no historical
-- rounds, tasks, contents, or versions are deleted or rewritten.
CREATE TABLE IF NOT EXISTS content_production_lines (
    id TEXT PRIMARY KEY,
    production_task_id TEXT NOT NULL UNIQUE
        REFERENCES production_tasks(id) ON DELETE RESTRICT,
    round_id INTEGER NOT NULL REFERENCES spiral_rounds(id) ON DELETE RESTRICT,
    content_id TEXT REFERENCES contents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending','producing','review_pending','awaiting_save','awaiting_publish',
        'verification_pending','published','closed','failed','recovering','aborted',
        'legacy_mapped'
    )),
    legacy_task_status TEXT,
    source_production_line_version_id INTEGER
        REFERENCES production_line_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_production_line_content
    ON content_production_lines(content_id) WHERE content_id IS NOT NULL;
-- One active content pipeline per tenant database (each tenant has an isolated DB).
-- Failed and published-but-not-closed lines intentionally continue to block a new line.
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_production_line_single_active
    ON content_production_lines((1))
    WHERE status IN (
        'pending','producing','review_pending','awaiting_save','awaiting_publish',
        'verification_pending','published','failed','recovering'
    );
CREATE INDEX IF NOT EXISTS idx_content_production_line_status
    ON content_production_lines(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_production_line_round
    ON content_production_lines(round_id, created_at DESC);

CREATE TABLE IF NOT EXISTS production_context_waivers (
    id TEXT PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES spiral_rounds(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL,
    waived_items_json TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    actor TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    consumed_task_id TEXT REFERENCES production_tasks(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_production_context_waivers_pending
    ON production_context_waivers(round_id, platform, consumed_task_id, created_at);
-- Enforce one unconsumed waiver per round/platform even across concurrent API workers.
CREATE UNIQUE INDEX IF NOT EXISTS idx_production_context_waivers_single_pending
    ON production_context_waivers(round_id, platform)
    WHERE consumed_task_id IS NULL;

CREATE TABLE IF NOT EXISTS production_context_snapshots (
    id TEXT PRIMARY KEY,
    content_production_line_id TEXT NOT NULL UNIQUE
        REFERENCES content_production_lines(id) ON DELETE RESTRICT,
    production_task_id TEXT NOT NULL UNIQUE
        REFERENCES production_tasks(id) ON DELETE RESTRICT,
    round_id INTEGER NOT NULL REFERENCES spiral_rounds(id) ON DELETE RESTRICT,
    master_plan_version_id INTEGER REFERENCES master_plan_versions(id) ON DELETE RESTRICT,
    sub_plan_id TEXT REFERENCES sub_plans(id) ON DELETE RESTRICT,
    sub_plan_version_id INTEGER REFERENCES sub_plan_versions(id) ON DELETE RESTRICT,
    strategy_step_id TEXT,
    latest_review_ref TEXT,
    context_status TEXT NOT NULL CHECK(context_status IN ('complete','waived','legacy_incomplete')),
    waiver_id TEXT REFERENCES production_context_waivers(id) ON DELETE RESTRICT,
    fingerprint TEXT NOT NULL,
    context_json TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_production_context_snapshots_plan
    ON production_context_snapshots(master_plan_version_id, sub_plan_id, sub_plan_version_id);
CREATE INDEX IF NOT EXISTS idx_production_context_snapshots_round
    ON production_context_snapshots(round_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS content_production_line_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_production_line_id TEXT NOT NULL
        REFERENCES content_production_lines(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    actor TEXT NOT NULL DEFAULT 'system',
    reason TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_production_line_events_line
    ON content_production_line_events(content_production_line_id, created_at DESC, id DESC);

-- A waiver is immutable except for its one-time consumption link.
CREATE TRIGGER IF NOT EXISTS trg_production_context_waivers_consume_once
BEFORE UPDATE ON production_context_waivers
WHEN NEW.id != OLD.id OR NEW.round_id != OLD.round_id OR NEW.platform != OLD.platform
 OR NEW.waived_items_json != OLD.waived_items_json OR NEW.reason != OLD.reason
 OR NEW.actor != OLD.actor OR NEW.evidence_json != OLD.evidence_json
 OR NEW.created_at != OLD.created_at OR OLD.consumed_task_id IS NOT NULL
 OR NEW.consumed_task_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'production_context_waivers are immutable except one-time consumption');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_context_waivers_no_delete
BEFORE DELETE ON production_context_waivers
BEGIN
    SELECT RAISE(ABORT, 'production_context_waivers are append-only');
END;

-- Map every existing per-content task to a legacy line. Do not claim old tasks have
-- the new full business context; preserve their original task status and input snapshot.
INSERT OR IGNORE INTO content_production_lines(
    id,production_task_id,round_id,content_id,status,legacy_task_status,
    source_production_line_version_id,created_at,updated_at
)
SELECT 'legacy-cpl-' || t.id,t.id,t.round_id,t.content_id,'legacy_mapped',t.status,
       t.production_line_version_id,t.created_at,t.updated_at
FROM production_tasks t;
INSERT OR IGNORE INTO production_context_snapshots(
    id,content_production_line_id,production_task_id,round_id,
    master_plan_version_id,sub_plan_id,sub_plan_version_id,strategy_step_id,
    context_status,fingerprint,context_json,captured_at
)
SELECT 'legacy-pcs-' || t.id,'legacy-cpl-' || t.id,t.id,t.round_id,
       t.master_plan_version_id,t.sub_plan_id,t.sub_plan_version_id,t.strategy_step_id,
       'legacy_incomplete','legacy:' || t.id,
       json_object(
         'schema_version',1,'legacy_incomplete',json('true'),
         'migration_note','历史任务只保留原任务输入与计划归属；未伪造缺失的经营上下文',
         'task_input',CASE
             WHEN json_valid(COALESCE(t.input_snapshot_json,'{}'))=1
             THEN json(COALESCE(t.input_snapshot_json,'{}'))
             ELSE json('{}')
         END,
         'legacy_raw_input_snapshot',COALESCE(t.input_snapshot_json,''),
         'round_id',t.round_id,'production_task_id',t.id,
         'source_production_line_version_id',t.production_line_version_id
       ),COALESCE(t.created_at,CURRENT_TIMESTAMP)
FROM production_tasks t;

-- All new per-content tasks receive a distinct line automatically. The Python
-- ProductionTaskStore attaches the complete immutable context snapshot in the same
-- transaction before the task can be claimed.
CREATE TRIGGER IF NOT EXISTS trg_content_production_line_after_task_insert
AFTER INSERT ON production_tasks
WHEN NEW.sub_plan_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM spiral_rounds WHERE id=NEW.round_id)
BEGIN
    INSERT INTO content_production_lines(
        id,production_task_id,round_id,content_id,status,
        source_production_line_version_id,created_at,updated_at
    ) VALUES(
        'cpl-' || NEW.id,NEW.id,NEW.round_id,NEW.content_id,'pending',
        NEW.production_line_version_id,COALESCE(NEW.created_at,CURRENT_TIMESTAMP),
        COALESCE(NEW.created_at,CURRENT_TIMESTAMP)
    );
    INSERT INTO content_production_line_events(
        content_production_line_id,event_type,from_status,to_status,actor,reason,created_at
    ) VALUES('cpl-' || NEW.id,'line_created',NULL,'pending','system',
             '为单条内容任务创建独立生产线',CURRENT_TIMESTAMP);
END;

-- Reject multi-item rounds at the task boundary as a final server-side invariant.
CREATE TRIGGER IF NOT EXISTS trg_production_tasks_single_item_ordinal
BEFORE INSERT ON production_tasks
WHEN NEW.sub_plan_id IS NOT NULL AND NEW.task_ordinal != 1
BEGIN
    SELECT RAISE(ABORT, 'one content task per production line; batch production is disabled');
END;

-- No worker may claim a task until its full business context is frozen.
CREATE TRIGGER IF NOT EXISTS trg_production_tasks_require_context_before_claim
BEFORE UPDATE OF status ON production_tasks
WHEN NEW.status='running'
 AND NOT EXISTS (
     SELECT 1 FROM production_context_snapshots s
     WHERE s.production_task_id=NEW.id AND s.context_status IN ('complete','waived')
 )
BEGIN
    SELECT RAISE(ABORT, 'production task is blocked: complete business context snapshot is missing');
END;

CREATE TRIGGER IF NOT EXISTS trg_content_production_line_transition_guard
BEFORE UPDATE OF status ON content_production_lines
WHEN OLD.status != NEW.status
 AND NOT (
    (OLD.status='pending' AND NEW.status IN ('producing','failed','aborted')) OR
    (OLD.status='producing' AND NEW.status IN ('review_pending','awaiting_save','failed','recovering','aborted')) OR
    (OLD.status='failed' AND NEW.status IN ('recovering','aborted')) OR
    (OLD.status='recovering' AND NEW.status IN ('producing','review_pending','failed','aborted')) OR
    (OLD.status='review_pending' AND NEW.status IN ('awaiting_save','awaiting_publish','verification_pending','failed','aborted')) OR
    (OLD.status='awaiting_save' AND NEW.status IN ('review_pending','awaiting_publish','failed','aborted')) OR
    (OLD.status='awaiting_publish' AND NEW.status IN ('verification_pending','failed','aborted')) OR
    (OLD.status='verification_pending' AND NEW.status IN ('published','failed','aborted')) OR
    (OLD.status='published' AND NEW.status='closed')
 )
BEGIN
    SELECT RAISE(ABORT, 'invalid content production line state transition');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_task_status_to_content_line
AFTER UPDATE OF status ON production_tasks
WHEN OLD.status != NEW.status
BEGIN
    UPDATE content_production_lines
    SET status=CASE
        WHEN NEW.status='running' THEN 'producing'
        WHEN NEW.status='succeeded' THEN 'awaiting_save'
        WHEN NEW.status='failed' THEN 'failed'
        WHEN NEW.status='cancelled' THEN 'aborted'
        WHEN NEW.status='pending' AND OLD.status IN ('running','failed') THEN 'recovering'
        ELSE status
    END,
    content_id=COALESCE(NEW.content_id,content_id),
    updated_at=CURRENT_TIMESTAMP
    WHERE production_task_id=NEW.id AND status!='legacy_mapped';
END;

CREATE TRIGGER IF NOT EXISTS trg_content_production_line_event_after_status
AFTER UPDATE OF status ON content_production_lines
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO content_production_line_events(
        content_production_line_id,event_type,from_status,to_status,actor,reason,created_at
    ) VALUES(NEW.id,'state_transition',OLD.status,NEW.status,'system',
             '生产任务或审核/发布事件驱动的状态迁移',CURRENT_TIMESTAMP);
    UPDATE content_production_lines SET
        closed_at=CASE WHEN NEW.status IN ('closed','aborted') THEN CURRENT_TIMESTAMP ELSE closed_at END
    WHERE id=NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_content_production_line_after_draft_insert
AFTER INSERT ON drafts
WHEN EXISTS (
    SELECT 1 FROM contents c
    JOIN content_production_lines l ON l.production_task_id=c.production_task_id
    WHERE c.id=NEW.content_id AND c.production_task_id IS NOT NULL
)
BEGIN
    UPDATE content_production_lines
    SET status='review_pending',updated_at=CURRENT_TIMESTAMP
    WHERE production_task_id=(
        SELECT c.production_task_id FROM contents c WHERE c.id=NEW.content_id
    ) AND status IN ('producing','awaiting_save','review_pending');
END;

CREATE TRIGGER IF NOT EXISTS trg_content_production_line_prevent_unreviewed_publish
BEFORE UPDATE OF publish_status ON drafts
WHEN NEW.publish_status='published' AND NEW.review_status!='approved'
 AND EXISTS (
     SELECT 1 FROM contents c
     WHERE c.id=NEW.content_id AND c.production_task_id IS NOT NULL
 )
BEGIN
    SELECT RAISE(ABORT, 'content must be approved before publish status can be published');
END;

CREATE TRIGGER IF NOT EXISTS trg_content_production_line_after_draft_review
AFTER UPDATE OF review_status,publish_status ON drafts
WHEN EXISTS (
    SELECT 1 FROM contents c
    WHERE c.id=NEW.content_id AND c.production_task_id IS NOT NULL
)
BEGIN
    UPDATE content_production_lines
    SET status=CASE
        WHEN NEW.publish_status='published' THEN 'verification_pending'
        WHEN NEW.review_status='approved' AND NEW.publish_status='not_published' THEN 'awaiting_publish'
        ELSE status
    END,updated_at=CURRENT_TIMESTAMP
    WHERE production_task_id=(
        SELECT c.production_task_id FROM contents c WHERE c.id=NEW.content_id
    ) AND status IN ('review_pending','awaiting_save','awaiting_publish','verification_pending');
END;

CREATE TRIGGER IF NOT EXISTS trg_content_production_context_snapshots_no_update
BEFORE UPDATE ON production_context_snapshots
BEGIN
    SELECT RAISE(ABORT, 'production_context_snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_content_production_context_snapshots_no_delete
BEFORE DELETE ON production_context_snapshots
BEGIN
    SELECT RAISE(ABORT, 'production_context_snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_content_production_line_events_no_update
BEFORE UPDATE ON content_production_line_events
BEGIN
    SELECT RAISE(ABORT, 'content_production_line_events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_content_production_line_events_no_delete
BEFORE DELETE ON content_production_line_events
BEGIN
    SELECT RAISE(ABORT, 'content_production_line_events are append-only');
END;
