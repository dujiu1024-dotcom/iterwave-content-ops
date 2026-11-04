# T9-D：早期 PR #1–#5 设计一致性审查报告

## 1. 审查范围与结论口径

- 设计验收基准：总控 Issue #7（当前主分支审查结论）及本分支新增的 `docs/architecture/ITERWAVE_DESIGN_BASELINE.md`。
- 代码基线：当前 `main`，截至 Migration 147；并逐项检查 PR #1–#5 的实际 diff、描述、文件、迁移和 GitHub Actions 证据。
- 早期 PR 均以 `main` 为 base，且目前均为 Draft/Open、未合并。
- 状态只使用：符合 / 部分符合 / 不符合 / 暂不适用。

**总评：PR #1–#5 的方向总体与 #7 的业务闭环一致，但均不能直接标记为“已完成”。PR #1 是待评审规范草案；PR #2–#5 是尚未进入 main 的分阶段实现。PR #4 是累积集成分支，携带 PR #2/#3/#5 的迁移与代码，因此不能把 PR #4 与其上游 PR 当作互不相关的独立变更重复合并。**

## 2. PR #1–#5 逐项审查

| PR | 状态 | 实际变更与依据 | 迁移/依赖 | 测试证据 | 结论 |
|---|---|---|---|---|---|
| #1 目标业务架构基线 | 部分符合 | 仅新增 `docs/architecture/ITERWAVE_TARGET_OPERATING_MODEL.md`，覆盖账号组北极星、母/子计划、子目标、逐条内容生产、上下文快照、发布复盘、图片重生成、版本治理、前端信息架构及端到端场景；文档自身状态为“待评审/后续开发依据” | 无数据库迁移；与 #7 的业务闭环高度一致，但不能以未评审文档取代 #7 | GitHub Actions run 41795814408，head `4ce7205`，success；文档检查通过不等于业务实现通过 | 业务方向符合；文档审批/替代关系尚未完成 |
| #2 子目标树与依赖门禁 | 部分符合 | 新增 `migrations/149_subgoal_tree_dependency_gates.sql`、`src/core/subgoal_store.py`、`src/api/subgoals.py`、SubgoalManager 与测试；PR 描述明确说明当时未接入 ProductionTaskStore/RoundExecutor，任务 3 接入留待后续 | 依赖 #3 的 Migration 148；PR 描述建议 #1→#3→#2→#5→#4 | GitHub Actions run 41800220394，head `6e7498`，success；早期 run 418002137? 以最新匹配 head 的 run 为准。通过不代表生产任务端门禁已在当前 main 生效 | 目标树/依赖模型实现方向符合；与生产任务闭环需通过 #5/#4 集成证明 |
| #3 北极星与资源基准治理 | 部分符合 | 新增北极星版本仓储、资源基准快照、母计划绑定、服务型业务模式兼容及相关测试；文件包括 `src/core/north_star_repository.py`、`resource_baseline_repository.py`、`migrations/148_account_group_north_star.sql` | Migration 148；为 #2/#5/#4 的前置迁移 | PR 描述记录 GitHub run 41800318584 成功；当前 GitHub branch 查询未发现对应 Actions run，且 PR head `bde54f3` 未合并 | 设计符合；CI/分支状态证据需以 PR 当前 head 重新运行为准 |
| #4 发布核验、独立复盘、经营数据回流和图片重生成 | 部分符合 | 包含发布凭证、内容哈希、Outbox、复盘调度、统一指标事实、经营缺口、图片重生成审核等；实际 diff 共 60 个文件，含 `migrations/151_publication_review_metric_feedback_visual.sql`、`migrations/153_fix_metric_facts_legacy_view.sql` 和大量 #2/#3/#5 依赖代码 | 明确依赖 Migration 148、149、150；PR 描述明确指出分支暂时携带 148–150 及上游必要代码，合并上游后重复差异应自然消失；明确不要单独执行 Migration 152；PR #6 已 superseded，不得与本 PR 并行迁入 | GitHub Actions run 41817904395，head `bfbc17a`，success；此前多个失败 run 后才有成功 run；仍需任务 3 Outbox 消费端端到端验收 | 功能范围与 #7 一致；累积分支/重复文件需按依赖顺序收敛，Draft 不可直接合并 |
| #5 单内容生产线与经营上下文快照 | 部分符合 | 新增 `content_production_lines`、上下文快照组装器、单条生产规则、历史任务映射、任务创建事务回滚和相关 API/Worker/前端；包括 `src/core/content_production_line_store.py`、`src/core/production_context.py`、`migrations/150_content_production_lines_and_context.sql` | 依赖 Migration 148、149；PR 描述建议 #1→#3→#2→#5→#4 | 当前 GitHub Actions branch 查询无该 head 的 workflow run；PR 描述中的 GitLab Pipeline 因身份验证未创建 Job，不能作为测试通过证据 | 单内容生产和快照设计符合；需在依赖合并后重跑 GitHub CI 并验收 Worker 实际执行路径 |

> 注：PR #2 当前 head 为 `6e749834243a8b1cbbeecd4e0d58905bb52f4930`，PR #3 head 为 `bde54f3596d4fbec7bd3eba669713de1fe600ee6`，PR #4 head 为 `bfbc17abc0b857bab8aaedb2366094accae1891e`，PR #5 head 为 `81f492f86ba21c9349f0f413e38bb6f25dec7b05`。所有 PR 当前均未合并。

## 3. 迁移顺序与重复变更审查

当前 main 的最高迁移版本为 147。早期 PR 声明的顺序为：

| 顺序 | PR | 迁移 | 作用 |
|---|---|---|---|
| 1 | #3 | 148 | 账号组北极星与资源基准 |
| 2 | #2 | 149 | 版本化子目标树、依赖与验收证据 |
| 3 | #5 | 150 | 单内容生产线与经营上下文快照 |
| 4 | #4 | 151 | 发布核验、Outbox、指标事实、复盘与图片治理 |
| 5 | #4 | 153 | 前向修复 Migration 151 中 metric_facts 兼容视图 |

关键风险：
- PR #2、#3、#5、#4 都以 main 为 base；其中 PR #4 的 diff 包含 148–150 的累积依赖代码，PR #5 也包含 148–150，PR #4 与上游 PR 存在大量相同文件和迁移内容。
- 不得将 PR #4 与 PR #2/#3/#5 的重复迁移/文件内容再次 cherry-pick 到同一分支。
- 推荐按 PR 描述顺序合并上游依赖：#1（规范评审通过后）→ #3 → #2 → #5 → #4。每一步合并后都必须更新后续 PR base/diff，确认重复迁移已从差异中消失。
- PR #4 的 Migration 153 是对已发布 151 视图的前向修复；保留 151 原文件，避免修改已应用迁移 checksum。
- PR #4 明确说明“不单独执行 Migration 152”；本仓库当前 main 也没有 152 文件。不得为了连续编号创建空迁移。
- PR #6 已被 PR #4 标记为 superseded，不能同时迁入第二套 Task 4 schema。
- PR #2 的子目标门禁与 PR #5 的生产任务接入必须一起验收；只合并子目标 Store 而不在生产任务创建/领取处强制调用门禁，不构成业务闭环。

## 4. 与 #7 设计条款的对照

| #7 设计条款 | PR #1–#5 的对应实现 | 状态 | 需要继续验证 |
|---|---|---|---|
| 经营资料与原始素材作为 AI 经营分析依据 | #3 资源基准、#4 经营诊断/素材上下文 | 部分符合 | 来源指纹变化后诊断失效，且审核/生成再次校验 |
| AI 分析报告用户确认后才能生成母计划 | 当前主干已有诊断报告门禁；#4 累积包含业务诊断 | 部分符合 | 与 T9-A 的生成原子性和审核时来源校验合并验收 |
| 北极星/母计划/子计划/子目标层级 | #3/#2 | 部分符合 | 迁移 148/149 在真实升级数据库上验证；历史数据不得推断归属 |
| 子目标依赖必须在生产任务创建/领取前生效 | #2 提供门禁服务，#5/#4 需要接入 | 部分符合 | API、Store、Worker 三层都要有实际负向测试，不能只测 SubGoalStore |
| 每条内容独立生产线、一次只生产一条 | #5/#4 | 部分符合 | 旧任务映射、并发活动线唯一性、任务失败/恢复和关闭后下一条决策 |
| 经营上下文完整且不可变 | #5/#4 | 部分符合 | 真实任务创建时快照齐全、失败原子回滚、Worker 执行前指纹/版本校验 |
| 用户发布确认、发布核验和 Outbox 关闭 | #4 | 部分符合 | T3 消费 Outbox 的 claim/ack/retry、重复事件、崩溃恢复和端到端关闭 |
| 独立复盘与统一指标回流 | #4 | 部分符合 | 真实零值、缺失值、失败采集、时区、历史批次和重试周期 |
| 图片重新生成必须有理由且人工采用 | #4 | 部分符合 | 浏览器验收单页文件哈希保护、整套/单页审核拒绝与重试 |
| 图文范围，不新增视频 | #1/#4 | 符合（范围声明） | 后续实现不得新增视频生产入口 |
| 历史计划/任务/证据保留 | #2–#5 的迁移与快照策略 | 部分符合 | 真实旧库升级、备份/恢复和旧任务不被新 Worker 领取 |

## 5. 唯一有效设计基线收敛

本 PR 新增 `docs/architecture/ITERWAVE_DESIGN_BASELINE.md`，统一业务层级、唯一业务闭环、版本/权限规则、生产上下文、单内容生命周期、发布复盘、图文范围、迁移和验收原则。

规范优先级：
1. `ITERWAVE_DESIGN_BASELINE.md`：本仓库唯一业务设计和验收规范入口。
2. Issue #7：记录原始总控约束、整改范围和执行状态；与基线冲突时必须先评审更新，不得由局部 PR 私自改规则。
3. `PLAN_VERSION_DATA_CONTRACT.md`、`unified-plan-management.md` 等领域文档：只细化数据/API/回滚契约，不得另立业务流程。
4. PR #1 的 `ITERWAVE_TARGET_OPERATING_MODEL.md`：来源草案；本基线合并后应标注“已被 ITERWAVE_DESIGN_BASELINE.md 取代”。
5. PR #2–#5 的设计文档：模块实现契约，不是同级产品基线。

## 6. 阻断项与执行要求

1. **依赖顺序阻断**：未按 148→149→150→151/153 顺序收敛前，不得将 PR #2/#4/#5 分别独立合并。
2. **PR #2 门禁集成阻断**：子目标门禁未被生产任务创建/领取端到端强制调用前，不得声称 T2 子目标闭环完成。
3. **PR #4 Outbox 消费阻断**：Outbox 生产端通过不代表消费者已可靠关闭单内容生产线；必须有任务 3 消费端的端到端证据。
4. **PR #3/#5 CI 证据阻断**：当前 PR head 缺少对应 GitHub Actions 成功 run；必须在最新 head 重新触发。
5. **所有 PR 的合并门禁**：Draft/Open 状态、代码 review、迁移顺序、CI 和真实业务端到端验收均通过后才允许合并。

上述属于早期 PR 合并/验收前置条件，当前已有 PR #1–#5 可以作为执行载体，不另建重复功能 Issue。若在后续 CI/集成中发现实际产品缺陷，再按 #22 约束另建 Issue。

## 7. 可复核的代码与测试入口

- PR #1：`docs/architecture/ITERWAVE_TARGET_OPERATING_MODEL.md`
- PR #2：`migrations/149_subgoal_tree_dependency_gates.sql`、`src/core/subgoal_store.py`、`src/api/subgoals.py`、`tests/test_subgoal_store.py`、`tests/test_subgoal_migration.py`
- PR #3：`migrations/148_account_group_north_star.sql`、`src/core/north_star_repository.py`、`src/core/resource_baseline_repository.py`、`tests/test_north_star_repository.py`、`tests/test_resource_baseline_repository.py`
- PR #4：`migrations/151_publication_review_metric_feedback_visual.sql`、`migrations/153_fix_metric_facts_legacy_view.sql`、`src/core/operating_feedback.py`、`tests/test_operating_feedback.py`、`tests/test_content_production_line.py`
- PR #5：`migrations/150_content_production_lines_and_context.sql`、`src/core/content_production_line_store.py`、`src/core/production_context.py`、`tests/test_content_production_line.py`

复核命令应在依赖迁移按顺序集成后的统一分支执行：

```bash
pytest -q tests/test_north_star_migration.py tests/test_north_star_repository.py \
  tests/test_subgoal_migration.py tests/test_subgoal_store.py \
  tests/test_content_production_line.py tests/test_operating_feedback.py \
  tests/test_plan_version_store.py tests/test_api_endpoints.py
```

> 该命令是合并后复核建议；不能把当前各 PR 分支的不同 CI 结果拼接成一次完整闭环通过。
