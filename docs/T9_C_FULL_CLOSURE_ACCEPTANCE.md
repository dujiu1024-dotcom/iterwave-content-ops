# T9-C：图文生产、审核、发布与效果反馈端到端验收状态

## 1. 当前结论

**状态：部分通过；主路径阻断已在上游 PR #33 修复，但尚未合并，完整发布/反馈/调整闭环仍未通过。**

本任务以 API 驱动的真实业务闭环为验收目标。现有代码和测试可以证明若干阶段分别可用，但尚不能从已确认诊断一路连续执行到效果反馈和策略调整建议。

本任务不通过 SQL 补写业务状态、不直接调用 Store 冒充 API、不跳过审核，也不使用真实外部平台发布。当前已发现的阻断必须先修复：

- [PR #33](https://github.com/dujiu1024-dotcom/iterwave-content-ops/pull/33) 已修复 #31 原始素材注册 API 422 和 #32 生产入口未创建 production_tasks；本 T9-C 分支直接基于 PR #33 当前最终 head `b99645d7cebd7c22882e625078be6560734605f7`，避免复现已修复的旧失败。PR #33 的素材隐私快照迁移已改用唯一编号 154，避开 PR #3 的 Migration 148。PR #33 已改为直接基于 PR #15；PR #27 已关闭为 superseded，其验收与修复提交保留在 PR #33 的分支历史中。PR #33 尚未合并，因此修复尚未进入 main。

此外，母计划 AI 生成 API 的 PR #14、T3-A 权限治理 PR #16、T4 发布核验/Outbox PR #4 均尚未合并；其实现不能当作 main 已具备的能力。

## 2. 目标闭环与当前证据

| 顺序 | 目标步骤 | 正式 API/服务 | 当前测试证据 | 状态 |
|---|---|---|---|---|
| 1 | 经营资料与图文素材 | `PUT /api/business-profile`、`POST /api/original-assets`、素材审核 | `tests/test_t8_diagnosis_api_acceptance.py`；PR #27/#33 的 `tests/test_t8_full_api_happy_path.py` | PR #33 已修复 #31；待依赖集成后复验 |
| 2 | AI 经营分析与用户确认 | diagnosis analyze/confirm API | `tests/test_business_diagnosis.py`、`tests/test_t8_diagnosis_api_acceptance.py` | 已有 API 级诊断门禁测试 |
| 3 | AI 母计划生成、编辑和审核生效 | PR #14 的母计划生成 API + `POST /api/plans/{id}/review` | `tests/test_master_plan_generation.py`、`tests/test_plan_version_store.py`；T9-A PR #29 | 部分符合；生成 API 未合入 main，T9-A CI 仍需通过 |
| 4 | 子计划/策略步骤审核生效 | `/api/subplans`、阶段 API、子计划 review | `tests/test_plan_version_store.py`、`tests/test_api_endpoints.py`、`tests/test_integration_reliability_subplan.py` | 模块/API 级覆盖存在；PR #16 尚未合入 main |
| 5 | 生产线审核激活 | `src/api/production_lines.py` review API | `tests/test_production_line_store.py`、PR #27 full API happy path | 部分符合；需在统一集成分支重新跑 |
| 6 | 正式生产入口创建任务 | `POST /api/subplans/{id}/rounds`、`GET /api/production-tasks` | PR #27/#33 `tests/test_t8_full_api_happy_path.py` | PR #33 已修复 #32，按 topic_count 创建带版本归属的 production_tasks；待依赖集成后复验 |
| 7 | 图文成品生成、修改、审核/退回/重提 | round/worker/content/review APIs | `tests/test_api_endpoints.py` 中 G2、central review、resubmit、round mutation 等测试 | 有模块/API 负向覆盖；尚未与同一 T9-C 任务链串联 |
| 8 | 用户发布确认与真实发布结果登记 | `POST /api/drafts/{id}/publish_confirm`、publish failure/sync APIs | `tests/test_api_endpoints.py` 中发布确认幂等、发布前质量门禁、拒绝/重提测试 | 有 API 级覆盖；尚未与本链路任务/内容 ID 串联 |
| 9 | 发布核验、Outbox 与生产线关闭 | PR #4 的 T4 发布核验凭证和 Outbox | PR #4 的 `tests/test_operating_feedback.py`、`tests/test_content_production_line.py` | 部分符合；PR #4 未合并，消费者端到端依赖仍需验证 |
| 10 | 效果数据与策略步骤反馈 | `POST /api/subplans/stages/{stage_id}/effect-feedback`、效果条件审核 | `tests/test_integration_reliability_subplan.py`、`tests/test_strategy_step_completion.py`；PR #4 的 operating-feedback tests | Store/模块覆盖存在；需同一任务链 API 级串联 |
| 11 | 复盘建议与策略调整采纳 | 周报建议/plan adjustment adoption API | `tests/test_api_endpoints.py` gap adoption tests；PR #4 的 operating feedback API | 部分符合；必须验证采纳只创建正式草稿，不直接修改有效战略 |

## 3. 已确认的阻断与依赖

### #31/#32 已修复但尚未合并

PR #33 将素材 API 请求模型正确注册到事务路由命名空间，并在生产入口事务中按请求数量创建幂等的 `production_tasks`，保留母计划版本、子计划版本、策略步骤、生产线版本和租户归属。另增加素材隐私快照迁移，并避免下游计划历史变化使已确认诊断来源指纹失效。

PR #33 当前最终 head 为 `b99645d7cebd7c22882e625078be6560734605f7`，并直接以 PR #15 的 `test/plan-to-content-e2e` 为 base；PR #27 已关闭为 superseded，不再是合并依赖。原 Migration 148 编号与 PR #3 冲突，现已改为 Migration 154。此前 head `f471140` 的 T8-E 专项和通用 CI 均通过；Migration 154 最新 head 的 T8-E run `41855080521` 与通用 CI run `41855080498` 正在重新执行，必须以这两次新运行作为最终验证。应按 PR #15 → PR #33 的顺序集成。

本 T9-C 分支直接以 PR #33 的最终 head 为 base。旧分支的 #31/#32 阻断记录保留为历史失败证据，不再描述为 PR #33 修复后的现状。当前 T9-C CI 必须重新证明完整 backend suite 和 T8-A/T8-E 专项在当前 head 上通过。

### 未合并的实现依赖

- PR #14：AI 母计划生成 API，T9-A PR #29 堆叠其上。
- PR #16：子计划基准/最新草稿权限保护，T9-B PR #34 堆叠其上。
- PR #4：发布核验、Outbox、复盘与经营指标回流；该 PR 还携带 migration 148–150 的累积依赖。
- PR #33：包含原 PR #27 的 T8-E 正式 API 主路径验收，并修复素材注册和生产任务创建。PR #33 直接基于 PR #15；PR #27 已 superseded。T9-C 的验收分支以 PR #33 为直接依赖。

## 4. 完成阻断后的验收路径

所有业务状态必须通过 API 写入。SQL 只允许创建必要的测试基础数据和只读断言。T9-C 的验收分支必须基于 PR #33 修复后的任务创建契约，而不是已失败的旧基线。

1. 使用临时 SQLite 数据库和 FastAPI TestClient；AI、平台、指标采集均使用确定性替身。
2. 通过正式 API 建立经营资料、素材和素材审核状态。
3. 通过诊断 API 生成并确认报告；记录报告 ID、版本和来源指纹。
4. 通过 AI 母计划生成 API 创建草稿；编辑/审核 API 使其生效。
5. 通过子计划、阶段和生产线 API 完成草稿审核及激活。
6. 通过正式生产入口创建轮次和 production_tasks；检查 task、round、tenant、母/子计划版本、策略步骤、生产线版本及素材授权快照。
7. 运行确定性 Worker/图文生成替身，产生内容成品；通过 API 审核、退回修改、重提和再次审核。
8. 通过 API 记录用户发布确认和模拟发布核验结果；不访问真实平台、不真实发布。
9. 通过 API 写入效果指标和阶段反馈，完成证据审核。
10. 生成可解释的复盘/调整建议；采纳必须走新版本草稿和审核生效流程。
11. 负向覆盖：重复请求、失败重试、并发、过期版本、跨租户 ID、素材许可撤销、审核拒绝、发布失败、Outbox 重复消费、历史快照不变。

## 5. 完成定义

- #31、#32 已由 PR #33 修复，且 PR #15/#33 已按依赖顺序合入目标基线；PR #33 retarget 后必须有新的最终 CI 证据。
- PR #14、#16、PR #4 等依赖在统一分支上完成集成，迁移顺序不重复。
- 至少一条完整 API happy path 从确认诊断到调整建议/采纳治理连续通过。
- 核心失败、权限负向、幂等、并发和历史快照均有自动化断言。
- 有独立 CI job、流程图、覆盖矩阵、完整报告和可复制命令。
- 本报告中的“阻断”全部解除后，才可关闭 Issue #25；不得以 PR #15/#27 的局部成功或模块单测拼接为全链路通过。


## 6. 既有验收任务复用与状态映射（2026-11-04 复核）

T9-C 不重复实现已经拆出的 T8 验收范围，按以下任务复用证据并补齐跨模块串联：

| 既有任务 | 覆盖范围 | T9-C 复用方式 |
|---|---|---|
| [#18 T8-B](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/18) | 内容生成、审核、发布确认、效果数据 API | 复用已有发布幂等、质量门禁、拒绝/重提测试；补充同一任务链的内容 ID/任务 ID 关联断言 |
| [#19 T8-C](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/19) | 策略步骤效果反馈、审核、调整建议与后续版本 | 复用效果审核和阶段状态测试；补充从 T9-C 发布内容回流到同一策略步骤的 API 链路 |
| [#20 T8-D](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/20) | 母计划生成失败原子性与 API 并发冲突 | 复用其失败注入/并发验收结果；T9-A 进一步补测详见 #38 |
| [#21 T8-E](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/21) | 经营分析到生产任务 API 主路径 | 复用 PR #33 合并后的主路径，待按依赖顺序集成后验证 |

当前仍需新增的 T9-C 证据不是重复测试每个独立 API，而是用真实 API 在同一临时租户/测试数据链中贯穿：生产任务 → 图文成品 → 审核/退回/重提 → 发布确认/核验 → 效果数据 → 阶段反馈审核 → 调整建议 → 新版本草稿治理。PR #4 Outbox 消费可靠性和早期迁移依赖仍须单独集成验收。

PR #37 当前变更仅是验收报告，不能单独证明上述闭环已通过。