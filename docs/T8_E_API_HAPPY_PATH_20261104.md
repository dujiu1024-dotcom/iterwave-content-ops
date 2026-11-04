# T8-E 经营分析至生产任务完整 API 主路径验收

## 目的与边界

补齐 T8-A 缺失的正向 API 主路径：经营资料与图文素材 → 经营分析确认 → 母计划审核生效 → 子计划审核生效 → 生产线审核激活 → 生产入口创建生产任务。

本验收通过公开 API 建立经营资料、原始素材、诊断报告、母计划、子计划、生产线、生产轮次和生产任务；不调用真实模型或外部平台。素材注册请求模型解析、轮次生产任务创建、素材隐私快照及诊断来源指纹自失效问题均在本修复分支处理，最终以 CI 的完整 API happy-path 结果为准。

## 自动化命令

    python -m pytest -vv tests/test_t8_diagnosis_api_acceptance.py tests/test_t8_full_api_happy_path.py
    python -m compileall -q src tests

测试使用 conftest 的临时 SQLite 数据库与 FastAPI TestClient。经营分析模型使用可控替身；母计划审核时的 AI 子计划拆解由可控替身模拟不可用，随后经正式子计划 API 创建并审核，避免真实模型/网络依赖。原始素材初始行暂作 fixture，不伪称已覆盖注册 API；当前生产入口只创建轮次，不创建可回读的生产任务，因此任务归属断言暴露 Issue #32。

## 正向链路覆盖矩阵

| 步骤 | API 操作 | 关键断言 |
|---|---|---|
| 1 | PUT /api/business-profile | 经营资料保存成功 |
| 2 | POST /api/original-assets、素材 review | 图文素材通过公开注册 API 创建，并通过审核 API 确认许可、隐私和质量状态 |
| 3 | POST diagnosis analyze/confirm | 报告由 draft 变为 confirmed，来源指纹有效 |
| 4 | POST /api/plans、plan review | 母计划由 draft 经审核变为 effective |
| 5 | POST /api/subplans、subplan review | 子计划及策略步骤经 API 创建并审核生效 |
| 6 | POST production-line draft/review | 生产线绑定精确子计划版本并审核激活 |
| 7 | POST /api/subplans/{id}/rounds | 正式生产入口开轮并绑定子计划版本、策略步骤、生产线版本 |
| 8 | GET /api/production-tasks?round_id=... | 按 topic_count 创建对应数量的待处理任务，且母/子计划版本、策略步骤、生产线版本归属一致 |
| 9 | POST/GET production-task assets | 任务保存素材版本、许可与隐私快照 |
| 10 | GET diagnosis/subplan/production-line | 通过 API 回读链路关联关系 |

## 负向门禁继承

现有 T8-A API 测试继续覆盖未确认/来源过期诊断不得创建母计划，以及待审核/权限未知素材不得进入任务。T8-E 进一步验证经 API 审核的素材许可与隐私状态被固化为任务快照。

## 关键一致性修复

- `TransactionalAPIRoute` 包装的素材路由使用主模块命名空间解析 Pydantic 注解；主模块必须显式导入所有素材请求模型。
- 生产入口在轮次创建事务内按 `topic_count` 创建独立生产任务，使用稳定幂等键，并复制轮次绑定的母计划版本、子计划版本、策略步骤和生产线版本。
- 素材使用快照同时固化许可与隐私状态；历史记录无法回溯的隐私快照使用 `unknown`，不伪造历史值。
- 诊断报告保留完整分析时计划历史快照，但计划历史是下游产物，不参与来源新鲜度指纹，避免诊断生成的计划和轮次反向使自身过期。

## 验收边界

- 所有被测业务对象均通过公开 API 创建或变更；不通过 SQL 写入被测业务状态。
- 不直接 SQL 写入母计划、子计划、生产线、轮次或生产任务；SQL 仅建立临时数据库 fixture。
- 模型调用可控，测试可重复执行。
- CI 结果以分支最新提交对应的 T8-E API happy path 检查为准。
- 若产品 API 缺陷导致测试失败，拆分至对应实现任务；验收 PR 不混入业务重构。


## Final stacked validation after dependency consolidation

PR #33 now targets PR #15 directly. The former PR #27 is superseded; its T8-E acceptance test changes are retained in this branch. The earlier T8-E and general CI runs on this head were recorded before the base-branch retarget and do not validate the final PR comparison. A fresh CI run on this updated branch is required before merge; passing this report's focused tests alone does not close Issues #21, #31 or #32.
