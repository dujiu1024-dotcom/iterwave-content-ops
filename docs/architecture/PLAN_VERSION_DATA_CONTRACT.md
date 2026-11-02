# 主计划与子计划版本数据契约（A 组）

> Migration: `115_plan_version_lifecycle.sql`
> Data access: 原生 `sqlite3`，`src/core/plan_version_store.py`
> Scope: 每个账号组自己的 `easel.db`；不得跨账号组共享业务记录。

## 1. 设计边界

- 继续使用 `plan_settings` 作为当前生效主计划的兼容读模型。
- `master_plan_versions` 是主计划版本事实表；快照一经创建不可原地覆盖。
- `signature_history` 保留原始签名审计轨迹。迁移时每条历史事件独立恢复，不按签名去重。
- 现有 `content_plans` 继续承担内容计划/子计划聚合及日历入口，不新增 `production_batches` 或 `production_items`。
- `content_plan_versions` 保存子计划的不可变快照；`content_plan_stages` 保存版本内的阶段和运行状态。
- `spiral_rounds` 记录实际消费的主计划版本、子计划版本和阶段，不在查询时推断当前版本。
- 发布完成量只按 `drafts.user_confirmed_at IS NOT NULL` 统计，不按“生成完成”或“草稿已保存”统计。
- 所有表均位于账号组独立数据库中，不重复添加 `account_group_id`。

## 2. 主计划版本

### `master_plan_versions`

| 字段 | 含义 |
|---|---|
| `id` | 内部版本 ID，轮次绑定使用 |
| `version_no` | 账号组数据库内递增版本号 |
| `plan_signature` | 复用 PlanVersionManager 的 SHA-256 规范签名 |
| `plan_snapshot` | 完整 JSON 快照 |
| `status` | `draft / effective / archived` |
| `source` | `user / review_adoption / legacy_recovery / system` |
| `based_on_version_id` | 创建草稿时参考的版本 |
| `reviewed_at/by` | 人工审核信息 |
| `effective_at` | 生效时间 |
| `superseded_at` | 被新版本替代时间 |
| `legacy_history_id` | 迁移恢复来源；新版本留空 |

数据库唯一部分索引保证最多只有一个 `effective` 版本。

### 生命周期

1. 用户编辑或复盘采纳：调用 `PlanVersionStore.create_master_draft()`。
2. 历史恢复：调用 `create_master_draft_from_version()`，只能创建新草稿，不得直接改写旧版本状态。
3. 人工审核通过：调用 `activate_master_version()`。
4. 生效时在一个 SQLite 写事务内归档旧生效版本、激活新版本、更新 `plan_settings.active_version_id` 和兼容快照，并追加 `signature_history`。
5. 任何轮次都不得因为主计划更新而改绑到新版本。

## 3. 子计划与阶段

现有 `content_plans` 是聚合记录；现有 `version` 是乐观锁版本号，不能单独作为历史快照。因此新增 `content_plan_versions`，并保留旧表。

- `content_plan_versions.plan_id` 指向既有 `content_plans.id`。
- `version_no` 是子计划历史版本号。
- `master_plan_version_id` 指向创建子计划时所依据的主计划版本。
- `snapshot_json` 保存该子计划完整快照。
- `content_plan_stages` 必须归属于一个明确的 `content_plan_version_id`。

阶段字段：
- `stage_order`：版本内排序，唯一。
- `target_count`：目标篇数。
- `start_date/end_date`：阶段时间段。
- `status`：`pending / in_progress / completed / paused / cancelled`。
- `notes`：阶段说明。

创建阶段变更应创建新的子计划版本；阶段执行状态可通过 `update_stage_status()` 推进。已完成或已取消阶段不可重新打开。

## 4. 轮次绑定契约

`spiral_rounds` 新增：

- `master_plan_version_id`
- `content_plan_version_id`
- `content_plan_stage_id`
- `plan_attribution_status`

新建轮次时必须在同一业务事务中绑定实际版本。使用 `PlanVersionStore.bind_round()`，传入明确版本 ID；不得通过“当前生效版本”在后续查询时补推。

历史归属：
- 有 `plan_signature` 且可匹配历史快照：绑定匹配版本，状态为 `resolved`。
- 缺少签名：状态为 `pending`，等待人工归属，不猜测。
- 有签名但找不到对应快照：状态为 `manual_review`，等待人工处理。
- 轮次已开始后，主计划新版本生效不改变其绑定。

## 5. 自动开轮幂等

`round_open_idempotency` 提供跨进程持久化租约：

- 同一个 `idempotency_key` 只允许对应同一个请求摘要和主计划版本。
- `reserved` 且租约未过期时，重复调用返回 `in_progress`。
- `created` 时重复调用返回已创建的 `round_id`。
- 失败或租约过期后允许重新取得租约。
- 调用 `complete_round_open()` 前，轮次必须已经绑定到租约指定的主计划版本。

B 负责在自动开轮 Worker 中接入 reserve → create/bind → complete 的顺序，并在异常路径调用 `fail_round_open()`。

## 6. 发布统计

视图 `content_plan_stage_publish_stats` 按阶段关联轮次、内容和草稿，只计入 `user_confirmed_at` 非空的草稿。

- 生成成功 ≠ 发布完成。
- 保存到平台草稿箱 ≠ 发布完成。
- 用户明确确认发布后，才增加阶段完成量。

## 7. A/B/C 集成约定

### A（数据库与版本数据层）
- 维护 Migration 115、`PlanVersionStore`、一致性审计和迁移测试。
- 不在 A 中复制 B 的业务编排逻辑。

### B（业务逻辑/API/Worker）
- `POST /api/plans` 改为创建草稿，不再直接覆盖 `plan_settings`。
- 新增草稿审核/生效 API；仅审核通过后调用 `activate_master_version()`。
- `POST /api/plan_adjustments/adopt` 只创建 `source='review_adoption'` 的新草稿。
- 新建轮次时绑定主计划版本；从子计划启动时同时绑定子计划版本和阶段。
- 自动开轮接入持久化幂等租约。
- 不得把旧轮次改绑到当前版本。

### C（前端）
- 展示草稿、生效、历史版本和审核动作。
- 复盘采纳后展示新草稿，不直接显示为已生效。
- 子计划编辑应携带版本号；阶段完成量使用用户确认发布统计。

## 8. 迁移与恢复

Migration 115 只增表、增列、增索引和视图；不删除旧表、不搬迁账号组数据库文件。

- `Database.initialize()` 在升级前按既有机制备份数据库。
- `schema_version` 记录迁移完成版本；迁移 SQL 通过 `_run_migration()` 在单事务内执行。
- 失败时回滚当前迁移；保留升级前备份用于恢复。
- 历史 `content_plans` 只恢复迁移时可见的当前快照，不伪造早期编辑历史。

## 9. 验收检查

- [ ] Migration 115 在旧库升级成功，重复执行不重复插入版本。
- [ ] `signature_history` 的每条事件均可追溯到 `master_plan_versions.legacy_history_id`。
- [ ] 无签名轮次为 `pending`；无法匹配签名为 `manual_review`。
- [ ] 生效新主计划后，旧版本仍存在且状态为 `archived`。
- [ ] 复盘采纳只创建草稿。
- [ ] 轮次绑定版本与 `plan_signature` 一致。
- [ ] 阶段只能绑定所属子计划版本。
- [ ] 阶段发布统计只计用户确认发布的草稿。
- [ ] 相同自动开轮幂等键不会生成两个轮次。
- [ ] 多账号组的数据库文件和版本记录完全隔离。
