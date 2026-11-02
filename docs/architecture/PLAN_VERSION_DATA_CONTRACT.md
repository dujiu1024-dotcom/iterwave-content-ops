# 主计划与平台子计划版本数据契约（A 组）

> Migration: `115_plan_version_lifecycle.sql`
> Data access: 原生 `sqlite3`，`src/core/plan_version_store.py`
> Scope: 每个账号组自己的 `easel.db`；不得跨账号组共享业务记录。
> 与 B 组契约：GitHub PR #101（B01-B31 业务流程/API/自动闭环契约）。

## 1. 设计边界

- `plan_settings` 保留为当前生效主计划的兼容读模型。
- `master_plan_versions` 是主计划版本事实表；快照一经创建不可原地覆盖。
- `signature_history` 保留原始签名审计轨迹。迁移时每条历史事件独立恢复，不按签名去重。
- 现有 `content_plans`、`content_plan_drafts`、`content_plan_rounds` 继续承担旧内容日历计划及其关联，完全不改造成子计划。
- 新增 `sub_plans` 作为独立的平台子计划身份；`sub_plan_versions` 保存不可变版本快照；`sub_plan_stages` 保存版本内阶段及运行状态。
- `spiral_rounds` 记录实际消费的主计划版本、子计划 ID、子计划版本、阶段 ID，以及子计划和阶段快照。
- 发布完成量只按 `drafts.user_confirmed_at IS NOT NULL` 统计，不按生成成功或草稿保存统计。
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

数据库唯一部分索引保证最多只有一个 `effective` 主计划版本。

### 生命周期

1. 用户编辑或复盘采纳：调用 `PlanVersionStore.create_master_draft()`；复盘采纳的来源必须是 `review_adoption`，不得直接写入 `plan_settings`。
2. 历史恢复：调用 `create_master_draft_from_version()`，只能创建新草稿，不得直接改写旧版本状态。
3. 人工审核通过：调用 `activate_master_version()`。
4. 生效时在一个 SQLite 写事务内暂停仍依附旧主计划版本的 active 子计划、归档旧生效版本、激活新版本、更新 `plan_settings.active_version_id` 和兼容快照，并追加 `signature_history`。旧轮次及子计划快照不变；子计划需创建基于新主计划版本的新草稿并审核后恢复 active。
5. 任何轮次都不得因为主计划新版本生效而改绑到新版本。

## 3. 平台子计划与阶段

### 子计划身份：`sub_plans`

- `id`：稳定子计划 ID。
- `master_plan_version_id`：当前子计划版本所依附的主计划版本指针；新主计划生效后，旧子计划版本仍保留原关联，新子计划版本审核生效时可将子计划身份切换到新主计划版本。
- `platform`：单个平台（xhs/douyin/kuaishou/wechat_channels）。
- `status`：`draft / active / paused / completed / archived / cancelled`。
- `current_version_id`：当前生效子计划版本。
- 已生效子计划应停用/归档，不物理删除。

### 子计划版本：`sub_plan_versions`

- `sub_plan_id`：所属子计划身份。
- `version_no`：该子计划内递增版本号。
- `master_plan_version_id`：创建该子计划版本时依据的主计划版本；不同子计划版本可以对应不同主计划版本。
- `snapshot_json`：完整子计划快照。
- `status`：`draft / effective / archived / rejected`。
- `based_on_version_id`：修订草稿的来源版本。
- `reviewed_at/by/review_note`：审核留痕。

同一子计划最多一个生效版本。修改生效版本必须创建新草稿；审核通过后旧版本归档，不能原地覆盖。新草稿可以基于新的生效主计划版本；审核生效时同步更新子计划身份的当前主计划版本指针，历史子计划版本仍保留旧主计划关联。

### 阶段：`sub_plan_stages`

阶段归属必须指向明确的 `sub_plan_version_id`。字段包括：

- `stage_order`：版本内排序，唯一。
- `title`、`direction`：阶段名称和内容方向。
- `target_count`：目标发布篇数。
- `start_date/end_date`：阶段执行窗口。
- `completion_condition`：完成条件。
- `status`：`pending / in_progress / completed / paused / cancelled`。
- `notes`：阶段说明。

阶段结构变更应创建新的子计划版本；阶段执行状态可通过 `update_stage_status()` 推进。已完成或已取消阶段不可重新打开。

## 4. 轮次绑定契约

`spiral_rounds` 新增：

- `master_plan_version_id`
- `sub_plan_id`
- `sub_plan_version_id`
- `sub_plan_stage_id`
- `sub_plan_snapshot`
- `sub_plan_stage_snapshot`
- `plan_attribution_status`

新体系轮次创建时必须绑定实际版本和阶段，使用 `PlanVersionStore.bind_round()`。子计划 ID、版本 ID、阶段 ID 必须同时提供；服务端校验三者归属一致，并把版本/阶段快照写入轮次。不得在查询时推断当前版本。

历史归属：
- 有 `plan_signature` 且可匹配历史快照：绑定匹配版本，状态为 `resolved`。
- 缺少签名：状态为 `pending`，等待人工归属，不猜测。
- 有签名但找不到对应快照，或同一签名对应多个候选版本且没有精确 round_id 历史事件：状态为 `manual_review`，等待人工处理，不按最新记录猜测。人工处理必须调用 `attribute_legacy_round()`，填写审核人和原因，并写入 `user_override` 审计记录；已归属轮次禁止再次改绑。
- 轮次已开始后，主计划新版本生效不改变其绑定。

## 5. 自动开轮幂等

`round_open_idempotency` 提供账号组数据库内的跨进程持久化租约，唯一键应由租户、子计划版本、阶段和业务执行槽组成：

- 同一个 `idempotency_key` 只允许对应同一个请求摘要和主/子计划版本、阶段。
- `reserved` 且租约未过期时，重复调用返回 `in_progress`。
- `created` 时重复调用返回已创建的 `round_id`。
- 失败或租约过期后允许重新取得租约。
- 调用 `complete_round_open()` 前，轮次必须已绑定租约指定的主计划版本、子计划、子计划版本和阶段。

B 负责在自动开轮 Worker 中接入 reserve → `create_round_once()`。该方法在一个 SQLite 写事务内创建轮次、固化主/子计划及阶段快照，并把幂等记录标记为 created；事务提交前崩溃不会留下半套轮次，提交后重试返回原 round_id。仅使用分离的 create/bind/complete 步骤不满足崩溃一致性要求。人工 force 入口也必须走同一授权和幂等校验。

## 6. 发布统计

视图 `sub_plan_stage_publish_stats` 按阶段关联轮次、内容和草稿，只计入 `user_confirmed_at` 非空的草稿，并按 draft_id 去重。

- 生成成功 ≠ 发布完成。
- 保存到平台草稿箱 ≠ 发布完成。
- 用户明确确认发布后，才增加阶段完成量。
- 多平台子计划分别统计，不跨平台冲抵。

## 7. A/B/C 集成约定

### A（数据库与版本数据层）
- 维护 Migration 115、`PlanVersionStore`、一致性审计和迁移测试。
- 不在 A 中复制 B 的业务编排逻辑。

### B（业务逻辑/API/Worker）
- `POST /api/plans` 改为创建主计划草稿，不再直接覆盖 `plan_settings`。
- 新增审核/生效 API；仅审核通过后调用 `activate_master_version()`。
- 复盘采纳必须创建新的主计划草稿，来源为 `review_adoption`；不能直接覆盖主计划，也不能自动审核生效。
- 使用 `sub_plans`、`sub_plan_versions`、`sub_plan_stages` 构建平台子计划业务流程；不得复用旧 `content_plans`。
- 新建轮次时绑定主计划版本、子计划版本和阶段快照。
- 自动开轮接入持久化幂等租约；所有旧自动入口不得绕过授权、暂停、阶段和幂等校验。
- Worker 使用轮次绑定的不可变版本快照，不读取最新草稿替代。

### C（前端）
- 展示主计划草稿、生效、历史版本和审核动作。
- 复盘采纳后展示新草稿，不直接显示为已生效。
- 平台子计划按平台独立展示，阶段编辑和审核必须落到明确版本。
- 旧内容日历计划继续兼容，不能与平台子计划混为同一资源。
- 阶段完成量使用用户确认发布统计。

## 8. 迁移与恢复

Migration 115 只增表、增列、增索引和视图；不删除旧表、不搬迁账号组数据库文件，也不修改旧 `content_plans` 结构。

- `Database.initialize()` 在升级前按既有机制备份数据库。
- `schema_version` 记录迁移完成版本；迁移 SQL 通过 `_run_migration()` 在单事务内执行。
- 失败时回滚当前迁移；保留升级前备份用于恢复。
- 历史 `signature_history` 逐事件恢复；无法归属的轮次标记待处理。
- 旧 `content_plans` 数据原样保留；没有子计划历史证据时不伪造子计划。

## 9. 验收检查

- [ ] Migration 115 在旧库升级成功，重复执行不重复插入版本。
- [ ] `signature_history` 的每条事件均可追溯到 `master_plan_versions.legacy_history_id`。
- [ ] 无签名轮次为 `pending`；无法匹配或签名匹配存在歧义的轮次为 `manual_review`；人工归属必须有审计记录且不可二次改绑。
- [ ] 生效新主计划后，旧版本仍存在且状态为 `archived`。
- [ ] 复盘采纳只创建草稿。
- [ ] 子计划独立于旧 `content_plans`，每个平台使用独立身份和版本。
- [ ] 轮次绑定版本与 `plan_signature` 一致，并保存子计划/阶段快照。
- [ ] 阶段只能绑定所属子计划版本。
- [ ] 阶段发布统计只计用户确认发布的草稿。
- [ ] 相同自动开轮幂等键不会生成两个轮次。
- [ ] 多账号组的数据库文件和版本记录完全隔离。
