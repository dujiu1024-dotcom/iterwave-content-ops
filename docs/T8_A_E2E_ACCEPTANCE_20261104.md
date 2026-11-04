# T8-A 计划到内容生产端到端验收基线

## 范围与结论

本报告定义可重复执行的集成验收门禁。当前仓库已有多个模块级测试和跨模块联调测试；本次将它们编排为独立的 T8-A CI job，避免把模块单测误报为全链路已通过。

**状态：部分覆盖，端到端业务验收尚未全部完成。** CI 必须通过所列自动化测试；未覆盖场景不得标记为通过。

## 环境与数据隔离

- Python 3.11，依赖通过 `pip install -e ".[dev]"` 安装。
- 测试使用 `tests/conftest.py` 的 `tmp_path` SQLite 数据库；不访问 `data/easel.db`。
- API 集成测试通过 FastAPI TestClient 注入临时数据库和测试认证。
- AI/外部平台调用不作为验收前置条件；涉及外部副作用的环节必须使用可控替身。

## 自动化执行命令

```bash
python -m pytest -vv \
  tests/test_business_diagnosis.py \
  tests/test_business_profile_repository.py \
  tests/test_original_asset_inventory_migration.py \
  tests/test_integration_reliability_subplan.py \
  tests/test_plan_version_store.py \
  tests/test_production_line_store.py \
  tests/test_production_task_store.py
```

## 场景覆盖矩阵

| Issue 场景 | 当前自动化证据 | 状态 |
|---|---|---|
| 经营资料/素材变更影响诊断指纹 | `test_business_diagnosis.py` | 已有模块级覆盖 |
| 原始素材版本、授权快照不可篡改 | `test_original_asset_inventory_migration.py` | 已有数据库约束覆盖 |
| 母计划/子计划版本审核与生效 | `test_plan_version_store.py`、`test_integration_reliability_subplan.py` | 已有覆盖 |
| 生产线绑定准确的子计划版本及步骤 | `test_production_line_store.py`、`test_integration_reliability_subplan.py` | 已有覆盖 |
| 生产任务排队、worker 执行和租户上下文 | `test_integration_reliability_subplan.py` | 已有跨模块覆盖 |
| 旧轮次保留原计划版本快照 | `test_integration_reliability_subplan.py` | 已有跨模块覆盖 |
| 依赖步骤/进入条件未满足时阻止开轮 | `test_integration_reliability_subplan.py` | 已有负向覆盖 |
| 诊断确认/过期后禁止创建母计划草稿 | `test_t8_diagnosis_api_acceptance.py`：未确认与来源已变化两种情况均返回 409，且不创建母计划版本 | 已覆盖 API 负向门禁 |
| AI 经营分析失败不覆盖既有确认报告 | `test_t8_diagnosis_api_acceptance.py`：模型故障注入，断言旧报告状态/版本不变 | 已覆盖诊断失败原子性；AI 母计划生成失败原子性仍待补 |
| 素材授权/隐私不满足时拒绝生产使用 | `test_t8_diagnosis_api_acceptance.py`：待审核素材被拒绝；人工确认许可/隐私/质量后创建不可变使用快照 | 已覆盖 API 级门禁 |
| 内容生成、审核、发布/效果记录完整串联 | 尚需新增真实 API 流程场景 | 未覆盖，不能判通过 |
| 策略效果反馈及后续调整建议 | 尚需新增效果闭环场景 | 未覆盖，不能判通过 |
| 并发修改返回明确版本冲突 | 需确认覆盖范围并补充 API 并发测试 | 待核实 |

## 本轮新增验收

新增 `tests/test_t8_diagnosis_api_acceptance.py`，通过真实 FastAPI TestClient 和临时 SQLite 数据库验证：

- 经营分析报告从 draft 到 confirmed 的 API 状态迁移。
- 业务资料发生变化后，原报告不能继续确认，并被标记为 stale。
- AI 分析模型失败返回 502，既有确认报告不被 superseded，也不产生半成品新版本。
- 未确认或来源过期的诊断报告均不能创建母计划草稿；过期报告被标记 stale。
- 待审核/权限未知素材不能挂入生产任务；完成授权、隐私和质量审核后，生产任务记录固定素材版本及授权快照。

以上用例通过独立 T8-A CI job 执行；CI 结果以本 PR 最新提交对应的 Actions 检查为准。

## 缺陷处理规则

发现产品逻辑缺陷时，记录复现步骤、实际/预期结果和所属模块，并拆分至对应 T4/T7/T3 任务；T8-A 仅保留验收测试、fixture、CI 编排和验收报告，不混入业务重构。

## 验收结论规则

只有在 CI 的 T8-A job 全绿、矩阵中所有“未覆盖”场景均补齐并通过、环境与命令可复现后，才能将任务标记为完成。当前报告是基线，不代表全链路已经通过。
