# B 组子计划业务实现说明

> 日期：2026-11-02  
> 依赖：A 组 Migration 115 / PlanVersionStore，分支 `feature/a-plan-version-data-20261102`  
> 实现分支：`feat/subplan-business-flow-20261102`  
> PR：#107（Draft，依赖 A 组先合入）

## 1. 实现原则

- `content_plans` 保持旧内容日历/聚合计划语义，不复用为平台子计划。
- 母计划和子计划的编辑均创建不可变草稿版本；只有审核接口可以切换 effective 版本。
- AI 结果只写入 draft；人工审核通过才构成自动生产授权。
- 阶段完成唯一依据是 `sub_plan_stage_publish_stats.confirmed_published_count`，其来源是 `drafts.user_confirmed_at IS NOT NULL`。
- 新生产轮次在数据库中绑定 `master_plan_version_id / sub_plan_id / sub_plan_version_id / sub_plan_stage_id` 和两个快照；排队任务只携带快照摘要，不把任意用户快照明文复制到任务 payload。
- 已绑定轮次执行时读取创建时的母计划/子计划/阶段快照，历史归属不会被后续版本改写。A 的母计划新版本激活会暂停旧母计划下的子计划，因此旧版本的未执行队列任务会 fail closed；暂停生产、暂停子计划、阶段完成/取消、阶段窗口过期或平台熔断也会在 Worker 启动前拦截。

## 2. API

### 母计划版本

| Method | Path | 行为 |
|---|---|---|
| GET | `/api/plans/versions` | 查询母计划版本历史 |
| POST | `/api/plans` | 创建母计划草稿，不立即生效 |
| POST | `/api/plans/{version_id}/review` | approve 激活；reject 将草稿归档并留审核原因 |
| POST | `/api/plan_adjustments/adopt` | 将复盘建议写成现有平台子计划的新草稿版本，不覆盖母计划 |

### 平台子计划

| Method | Path | 行为 |
|---|---|---|
| POST | `/api/subplans` | 手工创建子计划及首个草稿版本 |
| GET | `/api/subplans` | 按母计划版本、平台、状态分页 |
| GET | `/api/subplans/{id}` | 子计划及所有版本、阶段 |
| PATCH | `/api/subplans/{id}` | 创建不可变修订草稿 |
| POST | `/api/subplans/{id}/stages` | 新增阶段草稿 |
| PATCH | `/api/subplans/{id}/stages/{stage_id}` | 编辑阶段草稿 |
| DELETE | `/api/subplans/{id}/stages/{stage_id}` | 删除阶段（生成新草稿版本） |
| POST | `/api/subplans/{id}/stages/reorder` | 阶段排序（生成新草稿版本） |
| POST | `/api/subplans/{id}/review` | approve 生效并授予范围内自动执行授权；reject 留原因 |
| GET | `/api/subplans/{id}/progress` | 目标、确认发布数、剩余量、阶段状态 |
| POST | `/api/subplans/ai-draft` | 按平台调用模型生成草稿；校验失败返回人工创建入口 |
| POST | `/api/subplans/{id}/pause` | 暂停/恢复子计划授权 |
| POST | `/api/subplans/{id}/rounds` | 手工创建绑定当前有效阶段的轮次 |

### 生产控制、日历和历史

| Method | Path | 行为 |
|---|---|---|
| GET/POST | `/api/production/pause` | 独立的内容生产暂停开关，不复用运营动作 `ops_paused` |
| GET | `/api/production-line` | 分页查询轮次、阶段、版本、平台和确认发布数 |
| GET | `/api/rounds/{id}/plan-attribution` | 历史轮次反查母计划/子计划/阶段快照 |
| GET | `/api/schedule` | 既有日历草稿事件增加轮次、子计划、版本、阶段及阶段日期字段 |

## 3. 自动生产闭环

1. Scheduler 与 `POST /api/autogrow/run` 共用 `AutogrowController.tick()`。
2. 先检查生产暂停、活跃轮次、首轮手动种子、目标达成、连续失败、连续无发布、目标周期上限。
3. 再检查自动每日轮数上限和冷却；force 只跳过每日上限/冷却，不跳过授权、阶段目标、暂停、熔断和幂等。
4. 从所有 active 子计划中选择可执行阶段；阶段顺序按 stage_order，前序阶段必须完成。
5. 阶段剩余量 = target_count - confirmed_published_count。草稿生成、平台草稿保存、审核通过、数据采集均不增加进度。
6. 检查阶段日期窗口和平台熔断。
7. 通过 A 的持久化 `reserve_round_open` + `create_round_once` 创建轮次。自动与手工开轮共享账号组小时槽幂等键，防止两个入口并发创建两轮。
8. 轮次创建后才把 pending 阶段切为 in_progress，并将绑定快照传入全局队列。
9. Worker 再次校验生产暂停、子计划授权、阶段状态/日期窗口、平台熔断和快照摘要。
10. RoundExecutor 从轮次绑定的母计划快照执行，注入子计划定位、受众、策略、语气、阶段方向和完成条件作为平台生成提示；不再把当前最新母计划签名写回历史轮次。
11. 达到确认发布目标后，scheduler 推进阶段；最后阶段完成后子计划状态变为 completed。

## 4. 兼容与防绕过

- 一旦账号组存在未归档/未取消的子计划，旧 `POST /api/rounds` 不再创建无绑定轮次。
- 旧 `POST /api/rounds/{id}/run` 不允许无子计划绑定的轮次绕过新授权；绑定轮次还需校验平台、阶段剩余目标和执行日期。
- 旧 `POST /api/content-plans/{id}/start` 经由旧 create_round 入口，因此同样被新规则拦截。
- 旧的 `ops_paused` 仍只控制评论/关注等运营动作；内容生产使用独立 `production_paused`，避免错误复用。
- 母计划签名变化时，旧的子计划绑定轮次不被 legacy stale-plan 逻辑误标；A 会暂停旧母计划下的子计划，未执行任务由授权校验拦截，历史轮次仍保留原始版本快照。

## 5. 自动化测试

新增/更新覆盖：
- 阶段进度只依据用户确认发布数；
- 阶段完成与下一阶段推进；
- 持久化轮次开轮的 trigger_source / applied_feedback；
- 子计划创建、审核授权、进度 API、手工绑定轮次；
- 复盘采纳只创建子计划草稿，不激活/覆盖母计划；
- 内容生产暂停与运营暂停开关隔离；
- autogrow 测试夹具改为先创建已审核子计划，符合新的授权门槛。

执行命令（合入 A 后在完整分支运行）：

```bash
python -m pytest -q
python -m compileall -q src tests
```

当前通过 GitHub 连接器完成代码提交和 PR 创建，但尚未取得 GitHub Actions workflow run 结果；不得将上述测试用例描述为已通过。真机/平台账号测试仍由项目负责人验收。

## 6. 尚需明确的边界

- Migration 115 的阶段窗口是日期级 `start_date/end_date`，不是日内小时窗口；若产品要求精确到每天几点至几点，需 A 后续提供兼容迁移，不能把评论/关注运营时段误当作内容生产时段。
- `production_paused` 已有后端 API，但前端控制入口需由 C 接入。
- 日历 API 已返回阶段和版本元数据；日历 UI 的筛选/标签由 C 实现。
