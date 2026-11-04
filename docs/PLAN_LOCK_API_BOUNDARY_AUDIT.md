# T3-A：计划锁定与后端 API 权限边界审查

## 审查基线

- 仓库：`dujiu1024-dotcom/iterwave-content-ops`
- 基线分支：`main`
- 基线提交：`5a437838468b13f52fa22619046876f501a97bfa`
- 任务：[#11 T3-A：计划锁定与后端 API 权限边界专项审查](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/11)

本审查区分战略定义、生命周期/授权状态、生产执行数据和日历排期。只有计划管理 API 可以创建或审核战略版本；其他入口只能变更其职责范围内的运行状态或排期数据。

## 写接口矩阵

| 资源/入口 | 写接口或写入点 | 允许写入 | 必须拒绝/约束 |
|---|---|---|---|
| 母计划版本 | `POST /api/plans` | 创建新母计划草稿 | 不得直接生效；必须先有已确认且未过期的经营诊断 |
| 母计划审核 | `POST /api/plans/{version_id}/review` | 审核最新草稿、拒绝草稿 | 不得审核旧草稿、已删除版本或非草稿 |
| 母计划删除/恢复 | `DELETE /api/plans/{version_id}` | 软删除最新生效版本，并原子恢复上一份有效版本 | 不得删除非最新版本、重复删除或造成无生效母计划 |
| 平台子计划 | `POST/PATCH /api/subplans`、`PATCH /api/subplans/{id}` | 创建身份、生成基于当前版本的不可变草稿 | 不得更换既有子计划的母计划归属；基准必须是当前版本 |
| 子计划阶段 | `POST/PATCH/DELETE /api/subplans/{id}/stages...`、`POST .../reorder` | 通过新子计划版本修改阶段定义 | 不得原地修改战略字段；必须校验版本号、阶段归属、依赖关系 |
| 子计划审核 | `POST /api/subplans/{id}/review` | 审核最新草稿 | 不得审核旧草稿、过期基准或母计划已失效的版本 |
| 子计划授权状态 | `POST /api/subplans/{id}/pause` | 暂停/恢复生产授权 | 恢复时必须验证母计划及当前生效版本仍有效 |
| 策略步骤运行状态 | 阶段完成/条件审核/结果接口 | 写入实际执行结果、人工业务/效果判定 | 不得通过这些接口改写阶段战略定义；执行结果必须关联实际版本/阶段 |
| 生产线定义 | `POST .../production-line/ai-draft`、`POST .../production-line/draft` | 生成绑定当前有效子计划版本的生产线草稿 | 不得直接生效；定义必须覆盖当前版本全部策略步骤且顺序一致 |
| 生产线审核 | `POST /api/production-lines/{line_id}/versions/{version_id}/review` | 审核基准仍有效的生产线草稿 | 不得审核过期子计划版本或基准已变化的草稿 |
| 生产线状态 | `PATCH .../production-line/status` | 暂停、恢复、完成、取消等状态迁移 | 不得更换生产线定义；恢复必须验证父级授权和版本 |
| 内容日历/旧 ContentPlan | `POST /api/content-plans`、`POST .../start`、`POST .../drafts`、`DELETE ...`、归档/恢复 | 历史只读投影 | 创建、独立启动、手工挂接、生命周期变更均返回 409 |
| 内容日历排期 | `PATCH /api/content-plans/{id}` | 仅允许 `scheduled_date`，并要求当前 `version` | 标题、主题、目标数量、平台、状态等字段均拒绝 |
| 单篇草稿排期 | `POST/PATCH /api/schedule` | 仅修改草稿的 `scheduled_publish_at` | 不得触发生产、发布或修改计划定义 |
| 生产轮次 | `POST /api/subplans/{id}/rounds`、`POST /api/rounds/{id}/run` | 从有效母计划、子计划、策略步骤及生产线创建/启动生产 | 必须检查状态、阶段窗口、目标余量、熔断、幂等和版本绑定 |
| 旧轮次入口 | `POST /api/rounds`、`POST /api/content-plans/{id}/start` | 兼容旧数据读取/拒绝独立生产 | 已启用子计划体系时拒绝未绑定战略来源的轮次 |
| 生产任务 | `ProductionTaskStore.create_task`、队列 worker/recovery | 创建、领取、重试、完成、恢复执行任务 | 任务来源必须与轮次绑定版本/阶段/生产线一致；快照不得被后续计划覆盖 |
| 复盘建议 | `POST /api/plan_adjustments/adopt` | 生成子计划修订草稿 | 不得直接修改或生效战略计划 |
| 审计 | `plan_change_audit` | 仅追加版本变更记录 | 数据库触发器拒绝 UPDATE/DELETE |

## 已确认的后端保护

1. 母计划版本采用草稿—审核—生效流程，审核只接受最新未删除版本。
2. 子计划及生产线均使用版本化草稿；生产线审核校验基准版本、子计划当前版本和父级授权。
3. ContentPlan 已退化为历史批次投影；日历更新字段白名单仅包含 `version` 与 `scheduled_date`。
4. 生产轮次保存母计划版本、子计划版本、策略步骤和生产线版本及快照；生产任务创建时再校验与轮次来源一致。
5. 母计划删除采用软删除，并在事务内恢复上一份未删除的有效版本；计划变更审计表为 append-only。
6. 队列执行前校验配置指纹、计划签名、轮次绑定和阶段授权状态。

## 本次发现并修复

### 子计划草稿审核缺少“最新草稿/当前基准”约束

原有 `activate_subplan_version` 仅检查目标记录为 draft，以及关联母计划仍有效；没有像母计划审核那样检查目标是否为该子计划最新版本，也没有验证其 `based_on_version_id` 仍等于子计划当前生效版本。

因此，多个并发草稿中较旧的草稿可能被直接审核，覆盖较新草稿的决策；在服务层直接调用时，也缺少对草稿母计划归属和当前基准的完整保护。

修复内容：

- 创建子计划修订版本时，校验既有子计划绑定的母计划版本不可被替换。
- 创建子计划修订版本时，要求 `based_on_version_id` 与当前生效版本严格一致；初始草稿必须以空基准创建。
- 审核子计划版本时，只允许审核该子计划最新版本。
- 审核时再次验证草稿基准仍等于当前生效版本，防止草稿创建后父版本已变化。
- 增加 Store 级负向测试和真实 HTTP API 负向测试。

## 测试覆盖

新增/扩展：

- `tests/test_plan_version_store.py`
  - 禁止跨母计划重新绑定既有子计划。
  - 禁止基于过期子计划版本创建新草稿。
  - 多个并发草稿中仅最新草稿可以审核。
  - 新版本生效后，旧基准草稿不得覆盖当前版本。
- `tests/test_api_endpoints.py`
  - 通过真实 `POST /api/subplans/{id}/review` 请求验证旧草稿返回 409。
  - 验证最新草稿仍可通过 API 正常审核。
  - 验证内容日历拒绝战略字段修改，同时允许合法排期修改。

建议 CI/本地执行：

```bash
pytest -q tests/test_plan_version_store.py tests/test_production_line_store.py tests/test_production_task_store.py tests/test_api_endpoints.py
```

## 风险与边界说明

- 生产环境 SQLite 数据库和真实账号组的并发行为，仍需通过目标环境部署验证。
- 本任务不把执行状态（例如阶段进度、业务完成判定、效果评估）误认为战略定义；这些字段可由对应运行/复核接口更新，但必须留在其版本和审计链路中。
- 自动化测试通过不等同于真实平台生产链路验收；端到端任务由 T8-A 覆盖。
