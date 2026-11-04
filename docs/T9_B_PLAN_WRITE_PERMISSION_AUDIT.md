# T9-B：计划与内容生产全链路写入权限审查矩阵

## 1. 审查基线与结论

- 仓库：`dujiu1024-dotcom/iterwave-content-ops`
- 基线：`main` + 尚未合并的 PR #16（`audit/plan-lock-api-boundaries`）；PR #16 已补充子计划版本归属/最新草稿审核约束，本任务在其分支上继续审查。
- 关联：[#24 T9-B](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/24)、[#22 T9 总控](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/22)、[#11 T3-A](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/11)。
- 审查范围：API 路由、Store、全局队列、Worker、恢复/重试、租户运行时与自动化测试。

**总体结论：计划定义写入有版本化 Store 门禁；生产执行写入由 API/Store/Worker 分层校验；租户数据隔离以每租户独立 SQLite 数据库和请求上下文为基础。当前证据足以确认主要战略写入口已有保护，但不能据此声称每一个业务写接口都经过逐项动态负向验证。**

## 2. 统一权限模型

| 层 | 执行身份/边界 | 必须遵守的约束 | 代码依据 |
|---|---|---|---|
| HTTP 认证 | 共享访问口令签发的会话或脚本 Token | 口令启用后，除登录/状态外的 API 请求必须认证；POST/PUT/PATCH/DELETE 不允许匿名访问 | `src/api/main.py`：`access_guard_middleware`；`src/core/auth.py` |
| 租户上下文 | 当前运行时 `runtime_tenant_id` | 写请求必须携带 `x-tenant-id` 且与当前运行时一致；缺失或过期返回 409；切换期间拒绝并发请求 | `src/api/main.py`：`access_guard_middleware`、`_tenant_switch_gate` |
| 租户数据 | 每个租户独立 DB、配置、输出目录和账号命名空间 | API 使用当前租户连接；不能把其他租户的数据库连接或 ID 当作当前租户资源 | `src/api/main.py`：`_build_tenant_runtime_context`；`src/core/tenant_registry.py` |
| 领域 Store | API/Worker 的受信任后端调用者 | 负责状态机、版本基准、父子归属、幂等、快照和事务完整性；不能只依赖前端禁用按钮 | `src/core/plan_version_store.py`、`production_line_store.py`、`production_task_store.py` |
| 全局队列 | 任务创建时固化的 tenant_id 与 payload | Worker 必须按任务 tenant_id 构建上下文；重试继承原租户和原始执行快照 | `src/core/task_queue.py`、`src/core/task_worker.py` |
| 生产执行 | Worker 系统执行身份 | 执行前校验配置指纹、计划签名、轮次/子计划/阶段/生产线绑定；不以当前前台选中的租户替代任务租户 | `src/core/task_snapshot.py`、`src/core/task_worker.py` |

## 3. 全链路写入入口矩阵

| 领域 | HTTP 写入口 / 后台入口 | 后端门禁与写入边界 | Store/服务与审计/快照 | 自动化证据 |
|---|---|---|---|---|
| 母计划 | `POST /api/plans`、`POST /api/plans/{id}/review`、`DELETE /api/plans/{id}`、AI 草稿 API | 创建仅草稿；审核只允许最新未删除草稿；删除最新生效版本时原子恢复前序版本；AI 草稿需确认诊断与来源指纹 | `PlanVersionStore.create_master_draft/activate_master_version/delete_master_version`；`plan_change_audit` | `tests/test_plan_version_store.py`、`tests/test_api_endpoints.py`、`tests/test_master_plan_generation.py` |
| 子计划 | `POST/PATCH /api/subplans`、`POST /api/subplans/ai-draft`、`POST /api/subplans/{id}/review` | 只能基于有效母计划；普通修订不得更换母计划归属；草稿基准必须是当前版本；仅最新草稿可审核 | `PlanVersionStore.create_subplan_version/activate_subplan_version` | `tests/test_plan_version_store.py`、`tests/test_api_endpoints.py` |
| 策略步骤/阶段 | `POST/PATCH/DELETE /api/subplans/{id}/stages...`、`POST .../reorder`、阶段结果/条件审核 API | 必须校验子计划版本归属、阶段 ID 属于指定版本、期望版本号、阶段稳定标识及依赖；定义修改创建新版本，运行结果单独写入 | `PlanVersionStore.create_subplan_version`、阶段 outcome/condition review 服务 | `tests/test_plan_version_store.py`、`tests/test_api_endpoints.py` |
| 生产线 | `POST .../production-line/ai-draft`、`POST .../production-line/draft`、`POST /api/production-lines/{line}/versions/{version}/review`、状态 PATCH | 草稿绑定当前有效子计划版本；审核验证归属、最新基准及父级授权；暂停/恢复不得改定义，恢复须验证父级授权 | `ProductionLineStore`；生产线版本审计 | `tests/test_production_line_store.py`、`tests/test_api_endpoints.py` |
| 内容日历 | `POST/PATCH/DELETE /api/content-plans...`、归档/恢复/独立启动 | 旧 ContentPlan 仅作历史投影；战略字段和独立生产入口拒绝；合法排期修改仅允许白名单字段并校验 version | `src/api/main.py` 的 ContentPlan 兼容路由 | `tests/test_api_endpoints.py::test_content_calendar_api_only_allows_schedule_field_changes` 及旧日历拒绝用例 |
| 排期 | `POST/PATCH /api/schedule` | 仅更新单篇草稿的计划发布时间，不创建战略计划、不启动生产、不直接发布 | schedule 路由和草稿记录 | `tests/test_api_endpoints.py` 中 schedule 用例 |
| 轮次 | `POST /api/subplans/{id}/rounds`、`POST /api/rounds/{id}/run`、兼容旧 `POST /api/rounds` | 轮次必须绑定有效母计划、子计划、阶段、生产线版本；校验暂停、窗口、目标余量、熔断与幂等；启用子计划体系后旧入口拒绝绕过 | `PlanVersionStore.bind_round`、`round_open_idempotency`、队列 enqueue | `tests/test_task_snapshot.py`、`tests/test_round_task_queue.py`、`tests/test_api_endpoints.py` |
| 生产任务 | Worker 创建/领取/完成/失败/取消/恢复；API 人工恢复 | 创建时对照轮次绑定的母/子计划/阶段/生产线 ID；快照冻结；完成时校验产物属于该轮次和平台；重试须人工确认外部未执行 | `ProductionTaskStore`、`ProductionTaskRecovery`、`GlobalTaskQueue` | `tests/test_production_task_store.py`、`tests/test_production_task_recovery.py`、`tests/test_task_queue.py` |
| 内容草稿/审核 | 内容生成、编辑、审核、退回、重提、发布确认/失败/同步确认 | 内容必须属于当前租户；审核状态机约束；发布确认与生成/保存草稿区分；失败和重试保留原任务上下文 | 内容/草稿状态机、发布事件及效果回写 | `tests/test_api_endpoints.py`、`tests/test_activation_retention_api.py` |
| 效果反馈/复盘 | `POST /api/subplans/stages/{stage}/effect-feedback`、周报建议反馈、策略调整采纳 | 反馈必须关联实际阶段/版本；采纳只创建新修订草稿，不直接改变生效战略定义 | 阶段 outcome、plan adjustment 与审计事件 | `tests/test_api_endpoints.py`、`tests/test_plan_version_store.py` |
| Worker/后台任务 | Scheduler、Autogrow、全局 Worker、任务重试与过期恢复 | Worker 按队列 tenant_id 重建独立运行时；执行快照校验失败时不执行；重试保留 tenant_id、aggregate_id 和 payload 快照 | `task_queue.py`、`task_worker.py`、`task_snapshot.py`、`task_worker_service.py` | `tests/test_task_worker.py`、`tests/test_task_snapshot.py`、`tests/test_tenant_hot_switch_isolation.py` |
| 租户切换/备份恢复 | `POST /api/tenants/select`、备份创建/恢复 | 切换期间请求门禁；恢复前拒绝有活跃队列任务的租户；恢复流程需显式确认 | `TenantRegistry`、`TenantSwitchGate`、`tenant_backup.py` | `tests/test_tenant_switch_gate.py`、`tests/test_tenant_switch_acceptance.py`、`tests/test_tenant_backup.py` |

## 4. 已确认的强制保护

1. **API 全局认证与租户上下文**：口令启用后 API 请求统一认证；写请求必须声明当前租户，缺失/不一致会在路由执行前被拒绝。
2. **独立租户运行时**：每租户独立 SQLite 文件和运行时上下文；Worker 依据任务记录的 tenant_id 构造上下文，而非读取 UI 当前租户。
3. **母/子计划版本治理**：母计划草稿审核、删除恢复和审计；子计划普通修订不可跨母计划重绑，基准必须是当前版本，只有最新草稿可审核。
4. **生产线版本治理**：生产线草稿绑定当前子计划版本，审核时再次验证父级版本与授权状态。
5. **生产任务来源冻结**：任务创建时复制母计划/子计划/阶段/生产线绑定和输入快照；后续任务完成校验产物归属。
6. **执行前快照校验**：Worker 验证配置指纹、计划签名和轮次绑定；旧快照不能在计划或配置变化后继续执行。
7. **历史记录保留**：旧轮次/任务快照与审计记录不因新计划生效而重写；日历旧记录只作为历史投影。

## 5. 边界与剩余风险

### 5.1 当前认证不是 RBAC

当前访问控制是单一共享访问口令/会话，不是多用户角色权限系统。租户上下文校验能防止请求错用当前运行时以外的租户连接，但**不能证明某个已登录用户只被授权访问某个租户**。如果产品要支持多用户、多租户成员关系或审批角色，必须先引入用户身份、租户成员关系和角色授权模型；不能把 `x-tenant-id` 当作授权凭证。

### 5.2 审计执行人粒度不一致

部分计划/生产写入口使用 `user`、`web-ui` 或 `system` 作为 actor/author；队列取消等入口已能记录会话指纹。当前审计可以追踪操作类型、对象和版本，但并非所有写操作都能归因到稳定的人类用户身份。此项不应通过伪造用户 ID 补齐。

### 5.3 直接 Store 调用是受信任后端边界

Store 不承担 HTTP 会话认证；其职责是领域状态/版本/归属约束。生产代码中的直接调用只允许来自已认证 API 或受控 Worker。不能把测试或内部 Python 调用误判为公网可访问入口；也不能在未来新增 API 时绕过 Store 的校验。

### 5.4 覆盖完整性说明

本矩阵覆盖当前仓库的计划、策略、生产线、轮次、任务、内容审核发布、反馈、队列/Worker 和租户管理主入口。个别配置、素材、知识库、账号登录等非计划战略写接口不逐行纳入战略权限矩阵，但仍受全局 API 认证和租户上下文门禁。新增路由必须补充到本矩阵及对应测试。

## 6. 可复制验证命令

```bash
pytest -q \
  tests/test_plan_version_store.py \
  tests/test_api_endpoints.py \
  tests/test_production_line_store.py \
  tests/test_production_task_store.py \
  tests/test_production_task_recovery.py \
  tests/test_task_queue.py \
  tests/test_task_snapshot.py \
  tests/test_task_worker.py \
  tests/test_tenant_hot_switch_isolation.py \
  tests/test_tenant_switch_gate.py \
  tests/test_tenant_switch_acceptance.py
```

> 说明：以上是现有测试的建议组合命令；本任务 PR 的 CI 结果才是本次变更是否通过的实际证据。
