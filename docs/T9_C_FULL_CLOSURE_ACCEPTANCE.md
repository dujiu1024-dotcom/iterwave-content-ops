# T9-C：图文生产、审核、发布与效果反馈端到端验收状态

## 1. 当前结论

**状态：阻断，不能判定全链路通过。**

本任务以 API 驱动的真实业务闭环为验收目标。现有代码和测试可以证明若干阶段分别可用，但尚不能从已确认诊断一路连续执行到效果反馈和策略调整建议。

本任务不通过 SQL 补写业务状态、不直接调用 Store 冒充 API、不跳过审核，也不使用真实外部平台发布。当前已发现的阻断必须先修复：

- [#31 原始素材注册 API 对有效 JSON 返回 422](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/31)。
- [#32 正式生产入口创建轮次但未创建可归属的 production_tasks](https://github.com/dujiu1024-dotcom/iterwave-content-ops/issues/32)。

此外，母计划 AI 生成 API 的 PR #14、T3-A 权限治理 PR #16、T4 发布核验/Outbox PR #4 均尚未合并；其实现不能当作 main 已具备的能力。

## 2. 目标闭环与当前证据

| 顺序 | 目标步骤 | 正式 API/服务 | 当前测试证据 | 状态 |
|---|---|---|---|---|
| 1 | 经营资料与图文素材 | `PUT /api/business-profile`、`POST /api/original-assets`、素材审核 | `tests/test_t8_diagnosis_api_acceptance.py`；PR #27 的 `tests/test_t8_full_api_happy_path.py` | 部分符合；素材注册被 #31 阻断 |
| 2 | AI 经营分析与用户确认 | diagnosis analyze/confirm API | `tests/test_business_diagnosis.py`、`tests/test_t8_diagnosis_api_acceptance.py` | 已有 API 级诊断门禁测试 |
| 3 | AI 母计划生成、编辑和审核生效 | PR #14 的母计划生成 API + `POST /api/plans/{id}/review` | `tests/test_master_plan_generation.py`、`tests/test_plan_version_store.py`；T9-A PR #29 | 部分符合；生成 API 未合入 main，T9-A CI 仍需通过 |
| 4 | 子计划/策略步骤审核生效 | `/api/subplans`、阶段 API、子计划 review | `tests/test_plan_version_store.py`、`tests/test_api_endpoints.py`、`tests/test_integration_reliability_subplan.py` | 模块/API 级覆盖存在；PR #16 尚未合入 main |
| 5 | 生产线审核激活 | `src/api/production_lines.py` review API | `tests/test_production_line_store.py`、PR #27 full API happy path | 部分符合；需在统一集成分支重新跑 |
| 6 | 正式生产入口创建任务 | `POST /api/subplans/{id}/rounds`、`GET /api/production-tasks` | PR #27 `tests/test_t8_full_api_happy_path.py` | **不符合/阻断：#32，创建轮次后任务列表为空** |
| 7 | 图文成品生成、修改、审核/退回/重提 | round/worker/content/review APIs | `tests/test_api_endpoints.py` 中 G2、central review、resubmit、round mutation 等测试 | 有模块/API 负向覆盖；尚未与同一 T9-C 任务链串联 |
| 8 | 用户发布确认与真实发布结果登记 | `POST /api/drafts/{id}/publish_confirm`、publish failure/sync APIs | `tests/test_api_endpoints.py` 中发布确认幂等、发布前质量门禁、拒绝/重提测试 | 有 API 级覆盖；尚未与本链路任务/内容 ID 串联 |
| 9 | 发布核验、Outbox 与生产线关闭 | PR #4 的 T4 发布核验凭证和 Outbox | PR #4 的 `tests/test_operating_feedback.py`、`tests/test_content_production_line.py` | 部分符合；PR #4 未合并，消费者端到端依赖仍需验证 |
| 10 | 效果数据与策略步骤反馈 | `POST /api/subplans/stages/{stage_id}/effect-feedback`、效果条件审核 | `tests/test_integration_reliability_subplan.py`、`tests/test_strategy_step_completion.py`；PR #4 的 operating-feedback tests | Store/模块覆盖存在；需同一任务链 API 级串联 |
| 11 | 复盘建议与策略调整采纳 | 周报建议/plan adjustment adoption API | `tests/test_api_endpoints.py` gap adoption tests；PR #4 的 operating feedback API | 部分符合；必须验证采纳只创建正式草稿，不直接修改有效战略 |

## 3. 已确认的阻断与依赖

### #31 原始素材注册 API

有效 JSON 请求 `POST /api/original-assets` 返回 422「请求参数：不能为空」。因此当前 T8-E 测试只能通过 SQL fixture 建立初始素材行，无法声称覆盖正式素材注册 API。修复后必须用 API 建立素材，再通过素材审核 API 确认商业许可、隐私和质量状态。

### #32 正式生产入口未创建生产任务

`POST /api/subplans/{sub_plan_id}/rounds` 返回 201 并创建绑定版本的轮次，但 `GET /api/production-tasks?round_id=...` 返回空列表。缺少轮次到可追溯生产任务的正式 API 契约。没有 production_tasks 就无法通过真实生产任务触发图文生成、审核、发布和效果反馈，因此本任务后续步骤不能通过 Store/SQL 伪造任务继续。

### 未合并的实现依赖

- PR #14：AI 母计划生成 API，T9-A PR #29 堆叠其上。
- PR #16：子计划基准/最新草稿权限保护，T9-B PR #34 堆叠其上。
- PR #4：发布核验、Outbox、复盘与经营指标回流；该 PR 还携带 migration 148–150 的累积依赖。
- PR #27：T8-E 已经保留从经营资料/诊断到正式生产任务的 API happy-path 断言，并以 #31/#32 作为显式阻断。T9-C 应扩展该验收链，而不是复制一套绕过阻断的 fixture。

## 4. 完成阻断后的验收路径

所有业务状态必须通过 API 写入。SQL 只允许创建必要的测试基础数据和只读断言。

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

- #31、#32 已修复并有独立实现 PR/CI 证据。
- PR #14、#16、PR #4 等依赖在统一分支上完成集成，迁移顺序不重复。
- 至少一条完整 API happy path 从确认诊断到调整建议/采纳治理连续通过。
- 核心失败、权限负向、幂等、并发和历史快照均有自动化断言。
- 有独立 CI job、流程图、覆盖矩阵、完整报告和可复制命令。
- 本报告中的“阻断”全部解除后，才可关闭 Issue #25；不得以 PR #15/#27 的局部成功或模块单测拼接为全链路通过。
