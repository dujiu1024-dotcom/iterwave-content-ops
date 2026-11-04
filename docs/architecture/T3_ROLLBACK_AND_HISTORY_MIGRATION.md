# T3 单内容生产线迁移与回滚方案

## 1. 迁移策略

Migration 148 采用纯增量方式：

- 不删除、不改写旧 `production_lines`、`production_line_versions`、`spiral_rounds`、`production_tasks`、`contents` 和 `drafts`。
- 每条迁移前已存在的 `production_tasks` 映射为 `content_production_lines.status='legacy_mapped'`。
- 原任务状态保存在 `legacy_task_status`；原输入快照保留在 `production_context_snapshots.context_json.task_input`。
- 历史上下文明确标记 `legacy_incomplete`，不会伪装成新格式的完整快照，也不能通过新状态机领取执行。
- 新任务在创建事务内生成单内容生产线和完整上下文快照；任一必需事实缺失则整个任务创建事务回滚。

## 2. 上线前检查

1. 暂停 Autogrow 和人工生产入口。
2. 备份每个租户数据库，并验证备份可恢复。
3. 记录 `production_tasks`、`production_lines`、`production_line_versions`、`spiral_rounds`、`contents` 的数量。
4. 确认当前有效母计划、子计划、生产线版本和已确认经营诊断报告可读。
5. 应用 migration 148 后运行数据库完整性检查：
   - 每条历史任务均有 `legacy_mapped` 映射；
   - 每条生产线均有快照记录；
   - 历史快照状态为 `legacy_incomplete`；
   - 外键检查无错误；
   - 不存在多条活动生产线。

## 3. 回滚原则

回滚仅在需要恢复旧应用版本时执行。先停止 API、worker 和 Autogrow，并保存一份包含 T3 快照的新数据库备份。不要删除任何旧业务表或历史任务。

如果要完整恢复旧应用的生产行为，必须移除 migration 148 新增的状态机触发器、唯一活动线约束和新增表；只回滚应用代码而保留这些约束，会导致旧 worker 因缺少新上下文快照而无法领取任务。

## 4. 回滚 SQL

在每个租户数据库中执行以下 SQL（执行前必须备份）：

```sql
DROP TRIGGER IF EXISTS trg_content_production_line_after_task_insert;
DROP TRIGGER IF EXISTS trg_production_tasks_single_item_ordinal;
DROP TRIGGER IF EXISTS trg_production_tasks_require_context_before_claim;
DROP TRIGGER IF EXISTS trg_content_production_line_transition_guard;
DROP TRIGGER IF EXISTS trg_production_task_status_to_content_line;
DROP TRIGGER IF EXISTS trg_content_production_line_event_after_status;
DROP TRIGGER IF EXISTS trg_content_production_line_after_draft_insert;
DROP TRIGGER IF EXISTS trg_content_production_line_after_draft_review;
DROP TRIGGER IF EXISTS trg_content_production_line_prevent_unreviewed_publish;
DROP TRIGGER IF EXISTS trg_content_production_context_snapshots_no_update;
DROP TRIGGER IF EXISTS trg_content_production_context_snapshots_no_delete;
DROP TRIGGER IF EXISTS trg_content_production_line_events_no_update;
DROP TRIGGER IF EXISTS trg_content_production_line_events_no_delete;
DROP TRIGGER IF EXISTS trg_production_context_waivers_consume_once;
DROP TRIGGER IF EXISTS trg_production_context_waivers_no_delete;

DROP TABLE IF EXISTS content_production_line_events;
DROP TABLE IF EXISTS production_context_snapshots;
DROP TABLE IF EXISTS production_context_waivers;
DROP TABLE IF EXISTS content_production_lines;
```

新增索引随表删除自动移除。旧生产线和任务表不受影响。随后部署旧应用版本并执行数据库完整性检查。

## 5. 回滚后的数据恢复

- 原任务、轮次、内容、草稿和子计划生产线均保留原值。
- T3 快照/映射如需后续取证，从回滚前备份中读取，不应从旧表猜测或重建。
- 重新启用旧生产入口前，明确接受旧版本恢复批量生产语义；若继续使用 T3 业务规则，应重新部署本迁移和对应应用版本。
- 回滚不是删除历史记录的理由，也不得通过修改任务状态伪装成成功。

## 6. 自动化覆盖

`tests/test_content_production_line.py` 会在一份 pre-148 数据库副本上重新执行迁移，验证旧任务状态/输入不变、映射为 `legacy_mapped`、快照为 `legacy_incomplete`、外键完整，并验证旧任务不会在缺少新快照时被新 worker 领取。
