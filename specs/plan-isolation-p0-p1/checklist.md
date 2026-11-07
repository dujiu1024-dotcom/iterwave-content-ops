# 验收清单（P0 + P1）

## P0 计划隔离与归属

- [x] 迁移 167 可用：临时库 `Database.initialize()` 后 `contents` 含 `master_plan_version_id/sub_plan_id/sub_plan_version_id/sub_plan_stage_id/production_line_version_id/lifecycle_status`
- [x] `platform_plan_configs` / `master_plan_runtime_configs` 建表成功
- [x] `master_plan_versions.plan_snapshot` 不含 `per_platform` / `cadence`（临时库实测）
- [x] `plan_settings` 运行时投影仍含 `per_platform`（消费方兼容）
- [x] 母计划签名只覆盖战略字段：仅改 `per_platform` 签名不变（`test_signature_ignores_platform_execution_config`）
- [x] 平台配置变更只暂停对应平台子计划（`test_platform_only_change_pauses_only_that_platform`）
- [x] 有生效母计划时无绑定创建轮次返回 409 `PLAN_BINDING_REQUIRED`（`test_round_requires_binding_when_effective_master_exists`）
- [x] 绑定轮次写入五个归属字段（`test_bound_round_writes_attribution_and_orphans_on_failure`）
- [x] 轮次 `failed` 后未确认发布内容 `lifecycle_status='orphaned'`
- [x] 旧旁路 `create_round_record` 在有生效母计划时拒绝开轮
- [x] `save_plan` 遗留直写已停用并抛错

## P1 阶段指标 / 租户迁移

- [x] `stage_progress` 未配置指标 → `basis='content_reference'`，达成率 NULL
- [x] 配置指标但无数据 → `current_value/achievement_rate` 为 NULL 而非 0
- [x] 子计划阶段列表/详情返回 `progress`
- [x] `PlanCenter.tsx` 阶段卡片按指标展示，NULL 显示「—」，发布数标注「参考」
- [x] `TenantRegistry.create` 建组即建库并跑迁移（`test_tenant_create_initializes_database_schema`）
- [x] 服务启动遍历全部租户执行迁移，单租户失败不阻断

## 回归

- [x] spec 指定 11 个既有测试文件 + 新增测试 + 受影响旧测试：163 passed
- [x] `npx tsc --noEmit` 通过
- [x] 全量 `pytest`：除 7 个环境字体缺失用例（`Noto Sans CJK SC` 未安装，既有问题）外全部通过

## 第二轮审查补修

- [x] 绑定创建轮次写入 `sub_plan_snapshot` / `sub_plan_stage_snapshot`，`validate_integrity()` 不再报 `round_subplan_stage_mismatch`
- [x] `Production.tsx` / `Activation.tsx` 在计划模式 409 时提示到「计划中心 → 子计划阶段」创建轮次
- [x] 迁移 168 可执行：临时库 `sub_plan_stages.goal_id` 存在
- [x] 阶段指标完整配置经 `POST /api/subplans/{id}/stages` 落库；`stage_progress` 用 `goal_id` 计算达成率
- [x] 阶段指标残缺配置返回 422
- [x] 体验样稿导入已绑定轮次时 contents/drafts 归属与轮次一致
- [x] artifact adoption 补建 draft 归属与轮次一致
- [x] `attribute_legacy_round` 级联填充同轮 contents/drafts 归属
- [x] `tests/test_plan_isolation_attribution.py`：13 passed
- [x] `200 passed`（test_api_endpoints / round_executor_recovery / round_task_queue / integration_reliability_subplan / t8_master_plan_atomicity_api）
- [x] `50 passed`（plan_version_migration / master_plan_generation / unified_plan_management_migration / subplan_ai_provider / tenant_registry / tenant_runtime_switch / tenant_hot_switch_isolation）
- [x] `npx tsc --noEmit` 通过
