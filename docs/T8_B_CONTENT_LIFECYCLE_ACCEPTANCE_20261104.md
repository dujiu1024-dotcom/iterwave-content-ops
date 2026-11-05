# T8-B 内容审核、发布与效果数据 API 验收

## 目标与范围

本验收针对 Issue #18，使用 FastAPI TestClient 和 tests/conftest.py 的临时 SQLite fixture 验证内容审核、用户发布确认、效果数据回流和策略效果反馈。所有发布动作均为本地 API 状态登记，不调用真实平台发布接口。

## 环境与复现命令

- Python 3.11。
- 使用独立 tmp_path SQLite 数据库，不访问生产数据库。
- 认证通过测试口令 fixture 完成，不依赖开发机登录态。
- 模型和平台外部服务不被调用。

执行：

    python -m pytest -vv tests/test_t8_content_lifecycle_api_acceptance.py tests/test_t8_diagnosis_api_acceptance.py --durations=20 -o faulthandler_timeout=240
    python -m compileall -q src tests

## 覆盖矩阵

| 场景 | 自动化证据 | 状态 |
|---|---|---|
| 发布前不得录入效果数据 | test_content_review_publish_metrics_and_effect_feedback_api_chain：发布确认前 API 返回 409 | 已覆盖 |
| 内容审核通过状态迁移 | 真实 POST /api/contents/{id}/approve | 已覆盖 |
| 重复审核请求不重复写审核事件 | 重复 approve 后断言只存在一条 approved 事件 | 已覆盖 |
| 内容驳回、重新提交后才能再次审核 | test_content_review_rejection_requires_resubmission_before_approval | 已覆盖 |
| 用户确认发布 | 真实 POST /api/drafts/{id}/publish_confirm，不访问平台 | 已覆盖 |
| 重复发布确认幂等 | 重复调用 publish_confirm，断言返回 already_confirmed 且只记录一条发布事件 | 已覆盖 |
| 效果数据与内容、草稿、平台关联 | 真实 manual metrics API + snapshots 查询 | 已覆盖 |
| 效果数据回流策略步骤 | 真实 stage effect-feedback GET/POST，检查冻结证据包含发布数与指标值 | 已覆盖 |
| 生产任务 API 入队并由真实 worker 生成内容 | 需要将 T7 生产任务/worker 执行链纳入同一 TestClient 验收；本轮尚未覆盖 | 未覆盖 |
| 生产任务、轮次、计划版本到成品的完整归属链 | 当前用例从已有内容/草稿 fixture 起步；需要 API 主路径生成成品后再断言 | 未覆盖 |
| 效果数据重复提交/相同测量时间去重策略 | 当前 API 以追加快照方式记录；需明确重复请求契约后补充幂等断言 | 待核实 |
| 多租户内容、发布和指标隔离 | 本轮尚未建立两个租户的完整 API 上下文 | 未覆盖 |
| 失败时内容、发布状态、指标记录原子性 | 本轮尚未覆盖生产 worker 模型失败及效果数据写入失败注入 | 未覆盖 |

## 当前实现观察

- 审核、发布确认、手工指标记录、策略效果反馈均已有独立 API，可在隔离数据库中通过 TestClient 调用。
- 发布确认路径明确记录的是用户已经在外部平台完成的动作；验收不会自动点击平台发布。
- 手工指标记录采用追加快照模式；同一草稿可保留多个测量时间点，不能在没有明确幂等键契约时把合法的新测量误判为重复。
- POST /api/rounds/{round_id}/run 只负责将轮次加入全局任务队列。要满足 Issue #18 的完整 happy path，还必须在测试中执行真实队列 worker/生产执行器，并通过 API 查询最终成品，不能用 SQL 插入成品冒充生成。

## 结论

本次新增测试覆盖了审核 → 发布确认 → 指标回流 → 策略效果反馈的 API 下游闭环，并纳入独立 T8-B CI job。

**T8-B 尚不能关闭。** 完整生产任务 API/worker 生成路径、任务/计划版本归属断言、多租户隔离及失败原子性仍是明确缺口。不得将本报告中的下游闭环通过误报为 Issue #18 全部验收通过。
