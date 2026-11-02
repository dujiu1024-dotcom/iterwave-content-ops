# B 角色：子计划业务流程核查、API 契约与自动闭环测试设计（2026-11-02）

> 基线：`main` @ `9e4acc8766dcb2b9e40d653868bc1b1fde93e0ce`
> 状态：B-1 代码核查完成；B-2/B-4/B-5 API 与行为契约待 A 冻结数据结构后实现；本文是实施前契约，不代表业务功能已经上线。
> 范围：B01-B31。真机测试不在本交付范围内。

## 0. 关键结论与依赖

当前已有 `content_plans`、`content_plan_drafts`、`content_plan_rounds`、`spiral_rounds.plan_signature`、`signature_history`，但它们表达的是单个内容计划、草稿/轮次关联和母计划签名，不足以表达平台子计划、子计划不可变版本、阶段目标、审核授权、轮次阶段快照。

因此不能把旧 `content_plans` 直接改造成子计划，也不能在 A 冻结前先写依赖新字段的业务逻辑。A 至少需要冻结：
- 子计划身份、所属主计划、平台、状态和当前生效版本；
- 子计划版本快照及版本号；
- 阶段身份、顺序、目标篇数、方向、执行时间窗口、完成条件；
- 轮次与子计划 ID、子计划版本、阶段 ID 的不可变关联；
- 用户确认发布记录如何归属到阶段；
- 删除/归档/停用对历史轮次和版本的保留策略。

冻结后，B 负责迁移、API、业务服务、autogrow 和集成测试。若 A 的字段命名不同，以 A 最终冻结契约为准，不另造平行数据模型。

## 1. B-1 现有业务流程核查

### B01 autogrow 调用链 — P0

已确认调用链：
1. `src/core/scheduler/__init__.py` 注册 `autogrow_next_round`，默认每 60 分钟执行。
2. APScheduler runner `_run_autogrow_next_round` 通过 `_invoke` 派发 `_job_autogrow_next_round`。
3. `_job_autogrow_next_round` 调用注入的 `AutogrowController.tick()`。
4. `src/api/main.py` 在每个租户 runtime 构建时注入 AutogrowController；队列回调使用固定 tenant_id 调用 `_enqueue_round_task`。
5. `AutogrowController.tick()` 调用 `evaluate()`，通过后用 `create_round_record()` 插入 `spiral_rounds`，再提交全局 FIFO 队列。
6. `src/core/task_worker.py` 依据任务 tenant_id 重建租户上下文并执行 `RoundExecutor.run()`。
7. 还有一个用户手动入口：`POST /api/autogrow/run` 直接调用 `tick(force=True)`；`GET /api/autogrow` 只读展示判定与调度状态。必须把手动 force 入口也纳入子计划授权、阶段、暂停和幂等校验。

现状与缺口：
- 自动开轮有两个入口：scheduler -> `AutogrowController.tick()` 和 `POST /api/autogrow/run` -> `tick(force=True)`。人工轮次另有 `POST /api/rounds` 和 `POST /api/rounds/{id}/run`；旧内容计划另有 `POST /api/content-plans/{id}/start`。新子计划上线后必须明确这些旧入口的兼容/拦截策略，不能让旧入口绕过子计划授权。
- `create_round_record` 先提交轮次记录，再写母计划签名；新实现应在同一事务内写轮次和子计划版本/阶段快照，避免产生无绑定轮次。
- `AutogrowController.evaluate` 当前没有读取 `is_ops_paused()`，也没有检查内容生产的每日配额或生产时段。它有 autogrow enabled、活跃轮次、至少一轮成功种子、目标达成、连续失败、连续无发布、目标周期最大轮数、母计划轮数、每日自动轮数、冷却、平台熔断等保护。
- `force=True` 当前跳过每日轮数上限和冷却；不能允许其跳过新子计划授权、暂停、阶段目标达成、活跃轮次、熔断和幂等。
- `build_feedback()` 只把复盘建议转成下一轮评分/文案提示，不等于阶段推进，也不应修改子计划已生效版本。

实施要求：所有自动开轮必须统一经过子计划授权和阶段决策服务；旧的自动入口不得保留可绕过的新路径。

### B02 scheduler 调度 — P0

已确认：
- 默认 `autogrow_next_round` 每 60 分钟；`round_review` 每 10 分钟；`round_timeout_sweeper` 每 60 分钟。任务可由配置动态改成 cron 或 interval。
- cron 使用 `cfg.business_timezone`；interval 使用分钟。
- `_invoke` 将 scheduler job 放入全局队列，任务载荷包含 method/args，并绑定 tenant_id；`_run_tracked` 为任务运行写 task_runs 追踪。
- Worker 通过任务 tenant_id 获取租户 runtime；不会依赖当前前台活动账号组。
- round_run 入队时 `_build_task_execution_snapshot` 捕获配置指纹、母计划签名/快照和执行参数；Worker 执行前校验快照，过期任务 fail closed。

缺口：执行快照尚未包含子计划 ID、版本号和阶段快照。新轮次应在入队前从数据库轮次关联读取其子计划版本快照；Worker 校验的是轮次绑定的不可变版本，不应简单拿“当前最新草稿版本”替代。计划在队列等待期间发生编辑时，已审核版本应继续执行；只有被明确撤销授权/暂停的情况才按冻结规则拦截。

### B03 复盘采纳 — P0

已定位直接覆盖母计划的入口：
- `POST /api/plan_adjustments/adopt`（`src/api/main.py`）读取缺口维度，修改 `plan.dimension_allocation`，再调用 `PlanVersionManager.save_plan(..., author="web-ui:adopt-gaps")`。
- 这会直接写 `plan_settings`、追加 `signature_history`，并触发活跃轮次母计划签名失效逻辑。

新体系要求：复盘采纳不得静默覆盖主计划或已审核子计划。改为生成“子计划草稿修订建议”（或待人工确认的草稿版本），展示建议来源、影响的平台/阶段和差异；用户显式编辑并审核后才能生效。旧 endpoint 需要迁移为兼容提示或安全弃用，不得在新流程里被 UI 直接调用。保留母计划编辑的独立显式入口，避免把“采纳复盘建议”误当成“修改母计划”。

### B04 内容日历关联 — P0

现状：
- 日历 `web/src/pages/Calendar.tsx` 同时读取 `/api/schedule`（草稿排期）和 `/api/content-plans`（旧内容计划）。
- 草稿日历事件以 `drafts.scheduled_publish_at` 为主，未排期时以 `saved_at` 兜底；排期只允许修改 `draft_pending/draft_saved`，不会触发自动发布。
- 旧计划通过 `content_plan_rounds` 关联轮次，通过 `content_plan_drafts` 关联草稿；Worker 在生产成功后补写草稿关联并推进旧计划状态。
- `content_plan_rounds` 只有 plan_id/round_id/task_id/created_at，不包含子计划版本和阶段。

新体系需要保留现有草稿排期能力，同时在日历事件和生产线查询中增加 subplan_id、subplan_version、stage_id、平台、轮次。计划目标日期/阶段执行窗口与草稿实际排期时间必须分开表达，不能把“阶段计划时间”伪装成“已排期发布”。历史轮次通过关联表/快照反查原版本。

### B05 运营控制限制 — P0

现状调用顺序：
- `OperationsEngine` 的评论/关注/被动回复动作先检查全局 `ops_paused`，再检查平台熔断，再检查运营时段窗口、日配额和动作间隔。
- scheduler 的 `daily_ops` 也先检查全局暂停，再逐平台检查熔断、运营时段和 daily_quotas。
- 这些 rhythm.daily_quotas 是评论/关注等主动运营动作配额，不是内容生产篇数配额。
- `AutogrowController.evaluate` 目前不检查 `ops_paused`、rhythm 时段或上述运营配额；内容生产只有 autogrow 的每日轮数上限/冷却，平台熔断另行检查。
- `RoundExecutor._auto_schedule` 仅按母计划 publish_slots 给草稿写 scheduled_publish_at，不等于生产开轮时间窗，也不等于自动发布。

实现前需按产品语义区分两类控制：
1. 全局/租户“生产暂停”：必须在开轮前检查，暂停时禁止创建新轮；是否影响已入队未执行任务由 A/B 共同冻结为明确策略。
2. 生产配额/时段：使用子计划阶段的生产窗口与阶段篇数目标；不能错误复用评论/关注的 rhythm.daily_quotas。
调用顺序建议：租户上下文与暂停检查 -> 活跃轮次/任务检查 -> 生效子计划授权与版本校验 -> 当前阶段及发布进度 -> 阶段剩余目标 -> 平台可用性/熔断 -> 生产时间窗/配额 -> 冷却与每日开轮限制 -> 事务内幂等创建轮次和队列任务。若存在紧急停机，必须在入队前和 Worker 开始执行前双重检查。

## 2. B-2 子计划 API 契约（待 A 冻结后实现）

建议统一采用 tenant-scoped API；服务端从当前 runtime tenant 取租户身份，不接受客户端传 tenant_id。所有修改接口使用 `expected_version`（或 If-Match）做乐观并发控制。以下资源字段最终以 A 冻结的数据模型为准。

| 编号 | 建议 API | 行为/约束 |
|---|---|---|
| B06 | `POST /api/subplans` | 手工创建平台子计划草稿；关联主计划；不依赖 AI；初始状态 draft |
| B07 | `GET/PATCH /api/subplans/{id}` | 只修改草稿版本；生效版本不可原地改写，修改应生成新草稿版本 |
| B08 | `POST/PATCH/DELETE /api/subplans/{id}/stages/{stage_id}`；`POST /api/subplans/{id}/stages/reorder` | 阶段新增、编辑、删除、排序；校验排序唯一、目标数量合法、阶段归属一致；若已生效，改动只落入草稿 |
| B09 | `POST /api/subplans/{id}/review` | 审核通过时原子固化版本并标记 active；审核拒绝则记录原因；审核人和时间留痕。通过即授予该版本范围内的自动生产授权，不要求每轮复审 |
| B10 | `GET /api/subplans/{id}/progress` | 返回各阶段目标篇数、用户确认发布篇数、剩余篇数、状态、当前阶段；仅统计 `drafts.user_confirmed_at IS NOT NULL` 且关联到该子计划阶段的发布确认数据 |
| B11 | `GET /api/subplans?master_plan_id=&platform=&status=` | 按主计划、平台、状态过滤，分页 |
| B12 | 同 B06 | AI 不可用时，完整手工创建、编辑、阶段维护、审核路径仍可用；不依赖 AI 服务 |

约束：
- AI 输出不得调用 review/activate，不得直接写 active 状态。
- 编辑已生效子计划只能产生新草稿版本；新版本审核通过后再切换当前生效版本。旧版本和其轮次绑定永久保留。
- 删除仅允许删除从未生效且无轮次关联的草稿；已生效资源使用停用/归档，不物理删除历史。
- API 返回明确 404/409/422；跨租户 ID 不可见，避免越权探测。
- 阶段完成判定使用已确认发布计数，不使用生成草稿、已存平台草稿、审核通过数、数据采集完成数。

## 3. B-3 AI 拆解契约（待 A 冻结后实现）

- B13：只读读取主计划策略、目标、启用平台和约束；生成带来源摘要的解析上下文。
- B14：按平台生成子计划建议；每个平台独立输出，禁止把平台能力/篇数混成一个不可解释的总量。
- B15：每阶段至少包含方向/主题、目标发布篇数、建议执行窗口、阶段完成条件、阶段顺序及理由；数值必须能追溯到主计划目标。
- B16：服务端 schema 校验、平台枚举校验、篇数边界、时间窗口合法性、阶段顺序唯一、总量与主目标一致性校验；失败不得保存为生效状态。
- B17：AI 输出进入草稿，允许人工增删改阶段、调整目标与窗口；修改后重新校验。
- B18：模型超时、限流、返回格式错误或配置缺失时，返回可操作的降级错误码和手工创建入口；不阻塞 B12。

## 4. B-4 autogrow 阶段驱动契约（待 A 冻结后实现）

| 编号 | 行为要求 |
|---|---|
| B19 | 只有当前有效且授权未撤销的已审核子计划版本可触发自动开轮；无有效版本 fail closed |
| B20 | 依据子计划当前阶段状态和顺序识别唯一可执行阶段；禁止跳阶段、并行越序 |
| B21 | 阶段剩余量 = 阶段目标发布篇数 - 阶段已确认发布篇数；为 0 时停止当前阶段 |
| B22 | 当前阶段达成后，在事务中标记完成并切换下一阶段；没有下一阶段则子计划完成 |
| B23 | 每轮创建时固化 subplan_id、subplan_version、stage_id 和阶段快照；不能仅依赖当前版本查询 |
| B24 | 轮次创建使用事务锁 + 稳定业务幂等键/唯一约束；同租户同子计划版本同阶段的并发 scheduler tick 不得重复开出相同执行槽 |
| B25 | 在开轮和 Worker 执行前检查生产暂停、阶段窗口、生产配额、平台熔断；控制顺序见 B05 |
| B26 | 保留连续失败熔断、连续无发布暂停、目标达成停止、每日开轮上限、冷却期；新阶段状态机不得重置或绕过既有保护，除非有明确人工恢复审计 |
| B27 | evaluate 返回稳定 reason_code + 可读 reason + next_eligible_at + blocker details；页面可解释“未授权/暂停/阶段已完成/配额耗尽/非执行时段/平台熔断/冷却/已有活动轮次”等原因 |

发布数量 SQL 的权威口径必须是阶段关联内容对应的 drafts.user_confirmed_at 非空。重复确认发布接口已有幂等行为；阶段计数必须按 draft_id 去重，避免重复事件或多条审计事件重复计数。平台轮询识别到真实发布并通过统一 `on_user_published` 入口时也应计入；不能用 drafts.status='published' 单独替代 user_confirmed_at。

## 5. B-5 日历与生产线查询契约（待 A 冻结后实现）

- B28：日历计划事件可关联子计划和阶段；阶段目标日期/窗口与草稿实际 scheduled_publish_at 分开呈现。
- B29：复用既有轮次和草稿表，新增的关联/快照以 A 冻结模型为准；历史 round 不做重建，不覆盖旧计划关联。
- B30：提供生产线分页查询，支持 master_plan_id、subplan_id、platform、stage_id、version、round_status、date range 筛选；返回阶段目标、确认发布数、关联轮次和任务状态。
- B31：历史轮次详情必须能反查当时 subplan version/stage snapshot，即使该子计划已修改、停用或创建了新版本。

## 6. 自动闭环测试矩阵

### API 与授权
1. 手工创建子计划不依赖 AI 服务；字段边界/空平台/非法平台返回 422。
2. 草稿编辑使用正确 expected_version 成功；旧版本并发编辑返回 409。
3. 生效版本不能原地修改；编辑后生成新草稿，旧版本仍可查。
4. AI 生成结果状态只能是 draft；伪造 AI 响应尝试直接 active 被拒绝。
5. 审核通过原子固化版本；重复审核幂等；审核拒绝保留原因。
6. 无有效子计划时 autogrow 不创建 spiral_rounds、不入队。
7. 跨 tenant 访问子计划、阶段、版本均返回 404/拒绝，不能泄露资源存在性。

### 阶段进度与切换
8. 生成草稿数量增加不增加阶段进度。
9. 已存平台草稿数量增加不增加阶段进度。
10. 仅人工审核通过但未确认发布不增加阶段进度。
11. user_confirmed_at 非空的草稿计入一次；重复确认/重复事件不重复计数。
12. 多平台同一内容按子计划平台边界分别统计，不跨平台冲抵。
13. 阶段目标达到后不再为该阶段开轮；下一阶段只在前一阶段完成后激活。
14. 最后一阶段达成后子计划完成，scheduler 不再开轮。

### scheduler、幂等与任务上下文
15. scheduler tick 并发执行两次，只创建一条轮次和一个有效任务。
16. 同轮次队列任务重复入队复用活动 aggregate；不重复执行生产。
17. 两个 tenant 同 ID 资源互不影响；前台切换账号组不改变 scheduler 任务归属。
18. 入队后修改草稿版本不影响已绑定的已审核版本快照。
19. 子计划授权撤销/生产暂停后，尚未开始的 Worker 按冻结策略 fail closed；已开始轮次按策略收尾。
20. 轮次任务快照缺字段、版本不存在或签名不符时拒绝执行并留审计。

### 运营限制和熔断
21. 全局/租户生产暂停时不创建轮次、不入队。
22. 阶段执行窗口外不创建轮次，并返回 next_eligible_at。
23. 生产配额耗尽时不创建轮次；评论/关注配额不能错误影响内容生产。
24. 平台熔断时跳过该平台；全部平台熔断时不创建轮次。
25. 连续失败阈值触发后停止自动开轮；手工恢复后按恢复策略继续。
26. 连续无发布保护按用户确认发布数判定；草稿生成量不能解除保护。
27. 目标达成停止、每日开轮上限、冷却期均继续有效；force 不能绕过授权/暂停/阶段目标/熔断。

### 日历与历史
28. 日历可按阶段/版本/平台筛选；计划窗口和发布排期显示不同字段。
29. 已有 content_plans/calendar/round/draft 数据迁移后关联数量不减少。
30. 历史轮次展示其原始子计划版本和阶段快照；子计划新版本不会改变旧轮次信息。
31. 取消、归档、停用子计划不删除历史轮次、草稿、发布确认事件和任务记录。

## 7. B 任务状态

| 编号 | 当前状态 | 备注 |
|---|---|---|
| B01-B05 | 已完成代码核查 | 本文记录调用链、缺口和迁移约束 |
| B06-B12 | 待 A 冻结 | API 契约已拟定；不可先写依赖新表的实现 |
| B13-B18 | 待 A 冻结及 AI 契约 | 业务行为和降级测试已定义 |
| B19-B27 | 待 A 冻结 | 已确认当前 autogrow 缺生产暂停/配额/时段接入 |
| B28-B31 | 待 A 冻结 | 已确认旧日历只关联旧 content_plan/round/draft |

## 8. 验收门槛

B 全部完成需满足：
- API、AI、阶段状态机和 scheduler 全部使用 A 冻结的同一套子计划/版本/阶段数据结构；
- 阶段目标只按用户确认发布数量推进；
- 已审核子计划即为范围内自动执行授权，不逐轮重新审核；
- 所有自动入口统一经过授权、暂停、阶段和运营控制校验；
- 幂等开轮、任务快照、历史版本反查通过自动化测试；
- 旧日历和旧计划历史不丢失；
- 自动闭环测试报告列明测试命令、通过/失败数量和未解决风险。
