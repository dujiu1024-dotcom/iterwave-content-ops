# 母计划隔离 / 子计划与内容解耦 / 计划归属闭环（P0 + P1）

> 状态：待批准 · 2026-11-07
> 范围：用户已确认「P0 + P1 全修」
> 基线：`AGENTS.md` R2/R3/R5/R7/R14/R18/R21，`docs/architecture/PLAN_VERSION_DATA_CONTRACT.md`

## 1. 审查结论（有代码证据）

| 编号 | 问题 | 证据 |
|---|---|---|
| A1 | 母计划快照混入平台执行参数 | [plan_version.py](../../src/core/plan_version.py) `build_plan` 写入 `per_platform` / `cadence`，该对象被 `create_master_draft` 原样写入 `master_plan_versions.plan_snapshot` |
| A2 | 改一个平台篇数会让四平台一起失效 | [plan_version.py](../../src/core/plan_version.py) `compute_signature` 把 `platform_topic_counts` 拼进 hash |
| A3 | 母计划可由最近轮次反推 | `current_plan()` 取 `spiral_rounds.topic_ids` 条数反推 `topic_count_per_platform`，`load_plan` 返回 `source='derived'` |
| A4 | 只有一份当前母计划 | `plan_settings` 单行 id=1，多平台/多子计划共享同一投影 |
| B1 | 阶段进度=内容发布数 | `migrations/116_subplan_round_metadata.sql` 视图 `sub_plan_stage_publish_stats` 从 `contents/drafts` 聚合；前端 [PlanCenter.tsx](../../web/src/pages/PlanCenter.tsx) 阶段卡片直接展示该值 |
| B2 | 旧轮次入口可绕过子计划 | [main.py](../../src/api/main.py) `POST /api/rounds` 仅在 `active_subplans > 0` 时 409；`POST /api/rounds/{id}/run` 同样只在有活跃子计划时校验 |
| B3 | 内容表无计划维度 | `contents` 仅 `production_task_id`；`drafts` 无计划列。实测租户 `test-flow-20261106`：13 条 contents 全部 `production_task_id=NULL`，轮次 10 失败后内容仍残留 |
| C1 | 新租户建库不迁移 | [tenant_registry.py](../../src/core/tenant_registry.py) `create()` 只建目录；启动只初始化当前租户 |
| C2 | 遗留直写未封 | [plan_version.py](../../src/core/plan_version.py) `save_plan()` 仍可绕过版本审核直接写 `plan_settings` + `signature_history` |

已确认做对、本次不改：跨租户隔离与写守卫；`round_executor` 子计划分支按版本快照取数；子计划版本不可变。

## 2. 决策

- **D1 收口旁路**：存在生效母计划版本（`master_plan_versions.status='effective' AND deleted_at IS NULL`）时，创建/运行轮次必须绑定 `master_plan_version_id + sub_plan_id + sub_plan_version_id + sub_plan_stage_id + production_line_version_id`，缺一即 409。仅「无生效母计划且无活跃子计划」的冷启动租户保留旧插入路径。
- **D2 母计划快照只存战略字段**：`plan_snapshot` 仅保留 `topic_count_per_platform / dimension_allocation / enabled_platforms / content_format`。平台执行参数拆到独立表；`plan_settings.plan_snapshot` 仍输出运行时投影（战略字段 + `per_platform` + `cadence`），保证既有消费方（`platform_topic_count`、排期）不改行为。
- **D3 签名只覆盖战略字段**：`compute_signature` 删除 `platform_topic_counts`。平台级改动在激活时只暂停/失效对应平台子计划，不整体失效。
- **D4 内容必须可回溯计划**：`contents`、`drafts` 增加 5 个计划归属列，由 `round_executor` 从轮次行带入；历史数据按 `production_tasks` 回填。
- **D5 孤儿内容显式化**：轮次进入 `failed` / `cancelled` 时，把本轮未确认发布的内容 `lifecycle_status='orphaned'`，不再静默残留。
- **D6 阶段指标独立**：`sub_plan_stages` 增加计划指标字段；阶段进度以指标达成率为准，`confirmed_published_count` 只作参考与生产配额，不再作为"阶段进度"展示依据。NULL 一律显示「数据待采集」，禁止当 0（R21）。

## 3. 变更清单

### 3.1 新增迁移 `migrations/167_plan_isolation_and_attribution.sql`

```sql
-- 1) 母计划运行时配置（从 plan_snapshot 拆出）
CREATE TABLE IF NOT EXISTS master_plan_runtime_configs (
  master_plan_version_id INTEGER PRIMARY KEY,
  cadence_json TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_plan_configs (
  master_plan_version_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  topic_count INTEGER,
  publish_slots_json TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (master_plan_version_id, platform)
);

-- 2) 内容 / 草稿计划归属
ALTER TABLE contents ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE contents ADD COLUMN sub_plan_id TEXT;
ALTER TABLE contents ADD COLUMN sub_plan_version_id INTEGER;
ALTER TABLE contents ADD COLUMN sub_plan_stage_id INTEGER;
ALTER TABLE contents ADD COLUMN production_line_version_id INTEGER;
ALTER TABLE contents ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'
  CHECK (lifecycle_status IN ('active','orphaned','published','discarded'));

ALTER TABLE drafts ADD COLUMN master_plan_version_id INTEGER;
ALTER TABLE drafts ADD COLUMN sub_plan_id TEXT;
ALTER TABLE drafts ADD COLUMN sub_plan_version_id INTEGER;
ALTER TABLE drafts ADD COLUMN sub_plan_stage_id INTEGER;

-- 3) 历史内容按 production_tasks 回填
UPDATE contents SET
  master_plan_version_id = (SELECT t.master_plan_version_id FROM production_tasks t WHERE t.id = contents.production_task_id),
  sub_plan_id            = (SELECT t.sub_plan_id            FROM production_tasks t WHERE t.id = contents.production_task_id),
  sub_plan_version_id    = (SELECT t.sub_plan_version_id    FROM production_tasks t WHERE t.id = contents.production_task_id),
  sub_plan_stage_id      = (SELECT t.sub_plan_stage_id      FROM production_tasks t WHERE t.id = contents.production_task_id),
  production_line_version_id = (SELECT t.production_line_version_id FROM production_tasks t WHERE t.id = contents.production_task_id)
WHERE production_task_id IS NOT NULL;

-- 4) 阶段独立计划指标（NULL = 未配置/待采集，禁止 DEFAULT 0）
ALTER TABLE sub_plan_stages ADD COLUMN target_metric TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN target_value REAL;
ALTER TABLE sub_plan_stages ADD COLUMN metric_scope TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN baseline_value REAL;
ALTER TABLE sub_plan_stages ADD COLUMN baseline_at TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN metric_period TEXT;
ALTER TABLE sub_plan_stages ADD COLUMN data_source TEXT;

CREATE INDEX IF NOT EXISTS idx_contents_round_plan
  ON contents(round_id, sub_plan_stage_id);
CREATE INDEX IF NOT EXISTS idx_drafts_content_plan
  ON drafts(content_id, sub_plan_stage_id);
```

> 迁移必须幂等（重复执行不报错）；`ALTER TABLE ADD COLUMN` 前用 `PRAGMA table_info` 判断列是否已存在，已存在则跳过。

### 3.2 `src/core/plan_version.py`

1. `compute_signature`（约 L104-111）：删除 `platform_topic_counts` 拼接，payload 仅 `_SIGNATURE_FIELDS`。
2. 新增静态方法 `split_runtime_config(plan) -> tuple[dict, dict]`：返回 `(strategic, runtime)`，`runtime = {"per_platform": {...}, "cadence": {...}}`；`strategic` 只含 `_SIGNATURE_FIELDS` 对应键。
3. 新增方法 `platform_config_diff(prev: dict, next: dict) -> list[str]`：对比两份 runtime 的 `per_platform`，返回发生变化的平台列表。
4. `save_plan`（L178-206）：改为抛出 `RuntimeError("save_plan 已停用：母计划必须走 PlanVersionStore.create_master_draft + activate_master_version")`；删除写库逻辑。保留 `_has_plan_settings`、`load_plan`、`invalidate_changed_plans`。
5. `current_plan`：保留，仅用于冷启动 `derived`，补充注释说明不可作为生产计划来源；`invalidate_changed_plans` 增加 `AND master_plan_version_id IS NULL` 条件，避免误伤已绑定轮次。

### 3.3 `src/core/plan_version_store.py`

1. `create_master_draft`（L101-209）：
   - 落库前 `strategic, runtime = self._signer.split_runtime_config(snapshot)`；
   - `plan_snapshot` 写 `strategic`；签名用 `strategic` 计算；
   - 同事务写 `master_plan_runtime_configs(master_plan_version_id, cadence_json, ...)`（无 cadence 写 NULL）与 `platform_plan_configs` 每平台一行（`publish_slots_json` 无值写 NULL）；
   - 旧库无新表时（迁移未跑）跳过运行时表写入但保留战略快照，记录 warning，不阻断。
2. 新增 `load_runtime_config(version_id) -> dict`：读两表组装 `{"per_platform": {...}, "cadence": {...}}`；表不存在返回 `{}`。
3. `activate_master_version`（L309-420）：写入 `plan_settings.plan_snapshot` 前，用 `strategic + load_runtime_config(version_id)` 组装运行时投影；若 `active_version_id` 指向旧版且旧版有 runtime 配置，调用 `platform_config_diff`，仅对变化平台执行：
   - `UPDATE sub_plans SET status='paused' WHERE platform=? AND status='active'`；
   - 追加 `plan_change_audit`（`event_type='platform_config_changed'`，`change_reason` 含平台名）。
4. 新增 `stage_progress(stage_id) -> dict`：
   ```
   {"basis": "metric"|"content_reference",
    "target_metric": str|None, "target_value": float|None,
    "current_value": float|None, "achievement_rate": float|None,
    "confirmed_published_count": int|None, "data_ready": bool}
   ```
   - `target_metric/target_value` 均非空 → `basis='metric'`；`current_value` 从 `goals`/`metrics` 按 `metric_period` 与 `data_source` 读取，读不到为 `None`，`achievement_rate=None`（R21）。
   - 未配置指标 → `basis='content_reference'`，进度字段为 `None`，只返回 `confirmed_published_count` 供参考。
5. `bind_round`/`assert_stage_ready`/`reserve_round_open`/`create_round_once` 不改语义。

### 3.4 `src/api/main.py`

1. `CreateRoundRequest`（L1407）新增可选字段：`sub_plan_id: Optional[str]`、`sub_plan_version_id: Optional[int]`、`sub_plan_stage_id: Optional[int]`、`production_line_version_id: Optional[int]`。
2. `POST /api/rounds`（L4930-4979）：
   - 查询 `effective_master = SELECT id FROM master_plan_versions WHERE status='effective' AND deleted_at IS NULL`；
   - `effective_master` 存在时：四个绑定字段必须齐全，否则 409 `{"code":"PLAN_BINDING_REQUIRED"}`；
   - 用 `PlanVersionStore.assert_stage_ready(stage_id, version_id)` 校验阶段可执行；校验子计划 `status='active'`、`current_version_id=version_id`、`master_plan_version_id=effective_master.id`；校验 `production_line_version_id` 为该子计划的生效生产线版本；任一不符 409 `{"code":"PLAN_BINDING_INVALID"}`；
   - INSERT 增加 `master_plan_version_id, sub_plan_id, sub_plan_version_id, sub_plan_stage_id, production_line_version_id, plan_attribution_status='resolved'`；
   - `pvm.record(round_id, strategic_plan)`：`plan` 取 `plan_settings.plan_snapshot`（存在时），否则用母计划版本快照，禁止 `build_plan` 回退；
   - 无 `effective_master` 且 `active_subplans == 0` → 保留现有旧插入路径（冷启动），`plan_attribution_status='pending'`。
3. `POST /api/rounds/{round_id}/run`（L5007）：
   - SELECT 增加 `master_plan_version_id, production_line_version_id`；
   - 若 `effective_master` 存在：要求轮次五个绑定字段齐全，否则 409 `PLAN_BINDING_REQUIRED`；并校验与当前生效母计划一致（不一致 409 `PLAN_BINDING_STALE`，提示重新开轮）；
   - 删除 `if active_subplans and not all(...)` 的前置条件，改成上述无条件校验；
   - 无生效母计划时保留旧路径。
4. 启动初始化（约 L700-745）：构建当前租户上下文后，遍历 `registry.list()`，对每个租户 `paths = registry.paths(tenant_id, name=..., create=True)`，`Database(paths.db_path).initialize()` 后关闭；单租户失败记 `logger.exception` 并继续，统计写日志。
5. `POST /api/plans` / `GET /api/plans`：`GET` 在 `source='derived'` 时响应增加 `"derived_warning": "当前无生效母计划，展示值由最近轮次推导，不可用于生产"`。

### 3.5 `src/core/round_executor.py`

1. 运行开始时读取轮次归属（一次性）：
   ```python
   meta = self.conn.execute(
       "SELECT master_plan_version_id, sub_plan_id, sub_plan_version_id,"
       " sub_plan_stage_id, production_line_version_id FROM spiral_rounds WHERE id=?",
       (round_id,)).fetchone()
   ```
   保存为 `self._round_plan_meta`。
2. `INSERT INTO contents`（L1481-1498）增加 5 个归属列，值取 `self._round_plan_meta`；`INSERT INTO drafts`（L1569-1573）增加 4 列（`master_plan_version_id, sub_plan_id, sub_plan_version_id, sub_plan_stage_id`）。
3. 旧路径（无绑定）写入 NULL，不报错。

### 3.6 `src/core/state_machine/__init__.py`

`RoundStateMachine.transition` 在状态 UPDATE 成功并 `commit` 后，若 `target in ("failed", "cancelled")`：

```sql
UPDATE contents SET lifecycle_status='orphaned'
WHERE round_id=? AND lifecycle_status='active'
  AND id NOT IN (SELECT content_id FROM drafts WHERE user_confirmed_at IS NOT NULL)
```

同事务执行；失败只记日志，不改变状态迁移结果。

### 3.7 `src/core/tenant_registry.py`

`create()`（L296-318）：在 `paths = self.paths(...)` 后、写 registry 行前，`from .db import Database`（函数内导入避免循环），`db = Database(paths.db_path); db.initialize(); db.close()`；异常则 rollback 并抛 `TenantError("账号组数据库初始化失败: ...")`。

### 3.8 `src/api/subplans.py`

- 阶段列表（约 L966）与子计划详情（约 L1609）返回体增加 `progress`（`PlanVersionStore.stage_progress(stage["id"])`）；保留 `confirmed_published_count` 字段不变。
- 生产配额逻辑（L805/L896/L1213）**不改**，仍使用确认发布数。

### 3.9 `web/src/pages/PlanCenter.tsx`

- 阶段卡片（约 L753）：`progress.basis === 'metric'` 时主进度条显示 `achievement_rate`，标注「计划指标」；否则主进度位显示「数据待采集」灰色，另起一行小字「内容发布数（参考）：N」。
- `achievement_rate === null` 显示灰色「—」，禁止 `?? 0`。
- 页头副标题（约 L715）改为「历史版本仅作审计；当前生效母计划由 `plan_settings.active_version_id` 唯一确定」。

## 4. 测试与验收

必须通过（失败即回退改动）：

```bash
python -m pytest -q tests/test_plan_version_store.py tests/test_plan_version_migration.py \
  tests/test_master_plan_generation.py tests/test_t8_master_plan_atomicity_api.py \
  tests/test_integration_reliability_subplan.py tests/test_round_executor_recovery.py \
  tests/test_round_task_queue.py tests/test_tenant_registry.py tests/test_tenant_runtime_switch.py \
  tests/test_tenant_hot_switch_isolation.py tests/test_unified_plan_management_migration.py
```

需同步更新的既有测试（因 `save_plan` 停用）：

- `tests/test_audit_features.py:190`
- `tests/test_task_snapshot.py:78,91`
  改为直接写 `plan_settings`（测试内 SQL）或走 `PlanVersionStore`，不得调用已停用的 `save_plan`。

新增测试 `tests/test_plan_isolation_attribution.py`：

1. 有生效母计划时 `POST /api/rounds` 缺绑定 → 409 `PLAN_BINDING_REQUIRED`。
2. 绑定合法 → 轮次行五个归属字段齐全，`contents/drafts` 写入后归属字段与轮次一致。
3. 改单平台 `topic_count` → 母计划签名不变；该平台 `sub_plans.status='paused'`，其他平台不受影响。
4. `create_master_draft` 的 `plan_snapshot` 不含 `per_platform`/`cadence`；`plan_settings` 投影仍含（消费方兼容）。
5. 轮次转 `failed` → 未确认发布内容 `lifecycle_status='orphaned'`；已确认发布内容保持 `active`。
6. `stage_progress`：未配置指标 → `basis='content_reference'`；配置指标但无数据 → `current_value/achievement_rate` 为 `None`（不是 0）。

手工验收：

```bash
sqlite3 data/easel.db ".schema contents" | grep -E "sub_plan_stage_id|lifecycle_status"
sqlite3 data/easel.db "SELECT plan_snapshot FROM master_plan_versions ORDER BY id DESC LIMIT 1;"  # 不含 per_platform
sqlite3 data/easel.db "SELECT COUNT(*) FROM contents WHERE round_id=? AND sub_plan_stage_id IS NULL;"  # 绑定轮次应为 0
python -c "from src.core.tenant_registry import TenantRegistry; print('ok')"
```

## 5. 风险与边界

- 服务器现有测试租户（`test-flow-20261106`）存在无归属历史数据：迁移只回填有 `production_task_id` 的记录，其余保持 NULL，不伪造归属；UI 对 NULL 显示「未归属」。
- `plan_settings.plan_snapshot` 仍是单行投影，本次不改成"多平台各自一份当前计划"（A4 属结构性重构，超出 P0/P1；D2 已保证母计划版本不再混入平台参数）。
- 冷启动旧路径保留期间仍允许无计划生产，但只在租户完全没有生效母计划时；一旦有生效母计划即 409。

---

## 6. 第二轮审查补修（F1–F7）

> 触发：用户「再审查下，有没有其他问题」。以下均为对已完成 P0/P1 的收口，不扩大功能范围。

### F1（P0）`POST /api/rounds` 绑定分支缺计划快照，导致轮次可建但不可执行

现状：`src/api/main.py::create_round` 的绑定分支只写 5 个归属列，不写 `sub_plan_snapshot` / `sub_plan_stage_snapshot`；
`src/core/round_executor.py::_run_with_image_budget` 对二者执行 `json.loads(round_row[...])`，
NULL → `TypeError` → `RoundPreconditionError("轮次绑定的计划快照损坏")`。
同一缺陷还会让 `PlanVersionStore.validate_integrity()` 把这些轮次报成 `round_subplan_stage_mismatch`。

改法：

1. 在 `create_round` 绑定分支内查询：
   - `sub_plan_versions.snapshot_json`（条件 `id=? AND sub_plan_id=?`）
   - `sub_plan_stages.*`（条件 `id=? AND sub_plan_version_id=?`）
2. INSERT 增加 `sub_plan_snapshot` 与 `sub_plan_stage_snapshot`：
   - 前者原样写入 `snapshot_json`
   - 后者 `json.dumps(dict(stage_row), ensure_ascii=False, sort_keys=True)`（与 `plan_version_store.create_round_from_lease` 口径一致）
3. 任一查询为空 → 409 `{"code": "PLAN_BINDING_INVALID"}`。

验收：绑定创建的轮次两列非 NULL；`validate_integrity()["issues"]` 不含该轮次；`RoundExecutor.run` 能通过快照校验进入生产。

### F2（P0）前端两个创建入口在计划模式下必然失败且无可操作提示

`web/src/pages/Production.tsx::handleCreateRound` 调用 `api.createRound({topic_count})`，
`web/src/pages/Activation.tsx::promote` 调用 `api.createRound({topic_count, platforms})`。
二者都不带绑定字段，有生效母计划时被 409 `PLAN_BINDING_REQUIRED` 拒绝，用户只看到原始错误。

改法（不改后端契约）：

- `Production.tsx`：catch 中判断错误文案含「母计划」或「子计划阶段」，提示
  「当前账号组已启用生效母计划：请在「计划中心 → 子计划阶段」点击「开始本阶段生产」创建轮次」。
- `Activation.tsx::promote`：同样判断，提示
  「请先在计划中心创建可用的阶段生产轮次，再导入体验样稿」，并保持 `setError`。
- 不改 `createRound` 的签名，不伪造绑定；本轮不做"自动挑阶段"。

### F3（P1）阶段独立指标只有列、没有写入入口（P1-1 半落地）

迁移 167 建了 `sub_plan_stages.target_metric/target_value/metric_scope/baseline_value/baseline_at/metric_period/data_source`，
但 `PlanVersionStore.create_subplan_version()` 的 `INSERT INTO sub_plan_stages` 与
`src/api/subplans.py::_validate_stage_inputs` 都不接受/持久化这些字段 →
`stage_progress()` 永远返回 `basis='content_reference'`。

改法：

1. 新增 `migrations/168_stage_metric_config_and_attribution_backfill.sql`：
   - `ALTER TABLE sub_plan_stages ADD COLUMN goal_id TEXT;`（`goals.id` 实际为 TEXT，如 `goal-xxx`）
   - 回填（见 F6）。
2. `PlanVersionStore.create_subplan_version()`：
   - INSERT 增加 7 个指标列 + `goal_id`
   - 数值字段用 `float(...)` 校验，空串转 `None`，禁止写 0 代替未知（R3/R21）
3. `src/api/subplans.py::_validate_stage_inputs` 增加部分配置校验：
   - 出现 `target_metric` 或 `target_value` 任一 → 二者必须同时非空，`target_value > 0`
   - `metric_scope` ∈ {`delta`,`total`}；`data_source` 非空
   - `baseline_value` 与 `baseline_at` 必须同时出现或同时为空
   - `goal_id` 若存在必须是非空字符串（对应 `goals.id`）
   - 任一不满足 → 422
4. `stage_progress()` 取值顺序改为：`goal_id` →（兼容）`metric_period` 为纯数字时视作 goals.id → `target_metric` 最近一条 goal。
   文档明确 `metric_period` 只表示统计周期文本，不再承担 goals.id 语义。

### F4（P1）体验样稿导入正式轮次不写计划归属

`src/api/main.py::promote_activation_sample` 直接 `INSERT INTO contents/drafts`，不带归属列。
若目标轮次已绑定计划，导入内容计划归属为 NULL，与轮次归属不一致。

改法：用 `_load_round_binding(conn, round_id)` 取绑定；INSERT 时按 `PRAGMA table_info` 是否存在归属列动态拼列：
`contents` 写 5 列（`master_plan_version_id/sub_plan_id/sub_plan_version_id/sub_plan_stage_id/production_line_version_id`），
`drafts` 写 4 列。旧库缺列时保持原 SQL，不报错。

### F5（P1）artifact adoption 补建 draft 不写归属

`src/core/round_executor.py:1225` 的恢复路径 `INSERT INTO drafts` 缺 4 个归属列。
改为与正式路径一致，从 `self._round_plan_meta` 取值写入。

### F6（P1）人工归属不级联 + 迁移回填不完整

1. `PlanVersionStore.attribute_legacy_round()` 只更新 `spiral_rounds`，同轮 `contents/drafts` 仍 NULL。
   在同一事务内补：
   - `UPDATE contents SET master_plan_version_id=?,sub_plan_id=?,sub_plan_version_id=?,sub_plan_stage_id=?,production_line_version_id=? WHERE round_id=? AND master_plan_version_id IS NULL`
   - `UPDATE drafts SET ...`（`WHERE content_id IN (SELECT id FROM contents WHERE round_id=?)`）
   取值来自该轮次行（人工归属只解决母计划版本，其余字段随轮次已有值）。
2. `migrations/168_...sql` 回填：对 `round_id` 已绑定母计划但 `contents.master_plan_version_id IS NULL` 的记录，
   从 `spiral_rounds` 复制 5 列；`drafts` 再从 `contents` 复制 4 列。只回填 NULL，不覆盖已有值。

### F7（P2，本轮不修，记录为已知限制）

- `contents/drafts` 的归属列目前只有写入方，没有任何读取方；所有计划统计仍通过 `spiral_rounds` 关联（`sub_plan_stage_publish_stats` 视图）。
  当前一轮只绑一个阶段，因此统计口径等价；后续若出现"一轮多阶段"再切换读取路径。
- `contents.lifecycle_status` 只会被写成 `orphaned`，`published`/`discarded` 无写入方。
- `sub_plans.pause_reason` 无法区分「母计划整体切换」与「单平台配置变更」（CHECK 约束限制），仅审计事件可区分。

## 7. 验证（第二轮）

```bash
python -m pytest -q tests/test_plan_isolation_attribution.py tests/test_api_endpoints.py
python -m pytest -q tests/test_round_executor_recovery.py tests/test_round_task_queue.py
python -m pytest -q tests/test_plan_version_store.py tests/test_integration_reliability_subplan.py
npx tsc --noEmit
```

新增/扩展测试（写入 `tests/test_plan_isolation_attribution.py`）：

1. 绑定创建轮次后 `sub_plan_snapshot`/`sub_plan_stage_snapshot` 非 NULL，`validate_integrity()["ok"] is True`。
2. `promote_activation_sample` 导入到已绑定轮次后，`contents/drafts` 归属与轮次一致。
3. artifact adoption 补建 draft 归属与 `contents` 一致。
4. `attribute_legacy_round` 后该轮 `contents/drafts` 归属被级联填充。
5. 阶段指标完整配置 → 落库；`stage_progress` 走 `goal_id` 且无数据时 `achievement_rate is None`。
6. 阶段指标残缺配置 → 422。
