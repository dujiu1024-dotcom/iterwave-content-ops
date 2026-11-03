# T2 数据库迁移回滚说明

迁移：`142_unified_plan_management.sql`

该迁移仅向 `content_plan_rounds` 增加归属快照字段、索引和生产任务生产线版本门禁，不删除或重写原始计划、轮次、任务和内容数据。

## 回滚前

1. 停止应用和 Worker。
2. 备份当前租户 SQLite 数据库。
3. 确认没有其他代码依赖新增的归属字段。
4. 注意：回滚会移除 `content_plan_rounds` 中新增的归属快照；原始归属仍可从 `spiral_rounds` 及策略版本中恢复。

## SQLite 回滚 SQL

按以下顺序执行：

```sql
DROP TRIGGER IF EXISTS trg_production_tasks_require_line_provenance_update;
DROP TRIGGER IF EXISTS trg_production_tasks_require_line_insert;

DROP INDEX IF EXISTS idx_content_plan_rounds_line_version;
DROP INDEX IF EXISTS idx_content_plan_rounds_strategy;
DROP INDEX IF EXISTS idx_content_plan_rounds_master_version;

ALTER TABLE content_plan_rounds DROP COLUMN attribution_status;
ALTER TABLE content_plan_rounds DROP COLUMN production_line_version_id;
ALTER TABLE content_plan_rounds DROP COLUMN strategy_step_id;
ALTER TABLE content_plan_rounds DROP COLUMN sub_plan_stage_id;
ALTER TABLE content_plan_rounds DROP COLUMN sub_plan_version_id;
ALTER TABLE content_plan_rounds DROP COLUMN sub_plan_id;
ALTER TABLE content_plan_rounds DROP COLUMN master_plan_version_id;
```

要求 SQLite 3.35.0 或更高版本以支持 `ALTER TABLE ... DROP COLUMN`。若运行环境低于该版本，应在备份副本上重建 `content_plan_rounds` 原始表结构、复制原始四列数据，再替换原表；不要在生产库直接执行不受支持的语句。

该回滚不删除 `production_tasks` 中已存在的生产线版本归属，也不回退策略版本、生产线版本或历史内容。
