# 任务清单（P0 + P1）

## P0

- [x] T1 新增 `migrations/167_plan_isolation_and_attribution.sql`（幂等；两张运行时配置表 + contents/drafts 归属列 + lifecycle_status + sub_plan_stages 指标列 + 回填 + 索引）
- [x] T2 `src/core/plan_version.py`：签名去掉 `platform_topic_counts`；新增 `split_runtime_config` / `platform_config_diff`；`save_plan` 抛错停用；`invalidate_changed_plans` 限定未绑定轮次
- [x] T3 `src/core/plan_version_store.py`：`create_master_draft` 剥离并分表存运行时配置；`load_runtime_config`；`activate_master_version` 组装投影 + 仅失效变化平台子计划；新增 `stage_progress`
- [x] T4 `src/api/main.py`：`CreateRoundRequest` 增加绑定字段；`POST /api/rounds` 强制绑定；`POST /api/rounds/{id}/run` 强制校验；冷启动路径保留
- [x] T5 `src/core/round_executor.py`：`contents` / `drafts` 写入计划归属
- [x] T6 `src/core/state_machine/__init__.py`：`failed`/`cancelled` 时标记孤儿内容
- [x] T7 历史数据回填验证（迁移内 SQL）+ 临时库 schema 验证

## P1

- [x] T8 `src/api/subplans.py` 阶段列表/详情返回 `progress`
- [x] T9 `web/src/pages/PlanCenter.tsx` 阶段卡片按指标展示，NULL 显示灰色「—」，发布数降为参考
- [x] T10 `src/core/tenant_registry.py` `create()` 建库即迁移（`Database.initialize()`）
- [x] T11 `src/api/main.py` 启动遍历所有租户执行迁移（单租户失败不阻断启动）
- [x] T12 `PlanVersionManager.save_plan` 停用后同步修改 `tests/test_audit_features.py`、`tests/test_task_snapshot.py`

## 验收

- [x] T13 新增 `tests/test_plan_isolation_attribution.py` 覆盖 spec §4 的 6 条断言
- [x] T14 跑 spec §4 列出的 11 个既有测试文件，全绿（163 passed）
- [x] T15 手工验收命令（临时库 schema、`plan_snapshot` 不含 `per_platform`、投影保留平台配置）并输出证据

## 第二轮审查补修（详见 spec §6）

- [x] F1（P0）`src/api/main.py::create_round` 绑定分支补写 `sub_plan_snapshot` / `sub_plan_stage_snapshot`；缺快照 409 `PLAN_BINDING_INVALID`
- [x] F2（P0）`web/src/pages/Production.tsx` / `web/src/pages/Activation.tsx` 在 409 计划模式下给出可操作引导
- [x] F3（P1）`migrations/168_stage_metric_config_and_attribution_backfill.sql`（`sub_plan_stages.goal_id TEXT` + 归属回填）；`create_subplan_version` 落 7 指标 + `goal_id`（缺列的旧库自动跳过）；`_validate_stage_inputs` 部分配置校验；`stage_progress` 优先 `goal_id`
- [x] F4（P1）`promote_activation_sample` 写入 contents 5 列 / drafts 4 列归属（缺列兼容）
- [x] F5（P1）`round_executor` artifact adoption 补建 draft 写 4 列归属（抽为 `_ensure_draft_row`）
- [x] F6（P1）`attribute_legacy_round` 级联更新同轮 contents/drafts（缺列旧库自动跳过）；迁移 168 从 rounds 回填 NULL 归属
- [x] F7（P2）不修：归属列无读路径、`lifecycle_status` 仅 orphaned、`pause_reason` 不区分原因 —— 已在 spec §6 记录
- [x] F8 扩展 `tests/test_plan_isolation_attribution.py` 覆盖 spec §7 的 6 条断言（13 passed）
- [x] F9 跑 spec §7 测试命令 + `npx tsc --noEmit`：`200 passed`（5 个后端文件）+ `50 passed`（计划/租户 7 个文件）+ tsc 通过
