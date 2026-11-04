# T3 单内容生产线与 T4 发布核验接口契约

## 1. 目的与边界

T3 将“子计划策略模板”与“单条正式内容的执行生命周期”分离：

- `production_lines` / `production_line_versions`：继续保存子计划级策略步骤模板，作为历史兼容和配置来源。
- `content_production_lines`：每个 `production_tasks.id` 对应且仅对应一条单内容生产线。
- `production_context_snapshots`：生产任务创建时固化的不可变经营上下文。
- 一个租户数据库内，同一时刻只允许一条未关闭的单内容生产线处于活动状态。

T3 不实现平台发布、发布结果采集、发布指标读回或效果复盘算法。上述真实核验由 T4 负责，并通过本契约回写生命周期事件。

## 2. 生命周期状态

| 状态 | 含义 | 进入方式 |
|---|---|---|
| pending | 已创建单内容任务，等待执行 | 任务创建事务 |
| producing | 正在生产 | 任务领取 |
| review_pending | 内容已生成，等待审核 | 任务成功 / 草稿建立 |
| awaiting_save | 等待存稿 | 审核流程可显式使用 |
| awaiting_publish | 审核通过，等待发布 | drafts.review_status=approved |
| verification_pending | 已记录发布结果，等待 T4 核验 | drafts.publish_status=published |
| published | T4 已提供真实发布核验证据 | T4 publish_verified 事件 |
| closed | 发布核验闭环完成 | T4 closed 事件 |
| failed / recovering | 失败或恢复中 | 生产任务状态机 |
| aborted | 人工中止 | 明确中止事件 |
| legacy_mapped | 迁移前历史任务映射 | 迁移回填；不可视为完整上下文 |

只有 `closed` 和 `aborted` 释放下一条内容的执行名额。失败、已发布但未关闭等状态继续占用唯一活动线约束。

## 3. T4 必须遵守的事件接口

### 3.1 读取当前生产线

- `GET /api/content-production-lines?limit=50`
- `GET /api/content-production-lines/{line_id}`

返回数据包含：

- `id`、`production_task_id`、`content_id`、`round_id`、`status`
- `context_snapshot`：快照 ID、完整性状态、指纹、采集时间和最终上下文
- `events`：状态迁移审计事件
- 任务状态、执行尝试次数和错误信息

T4 应以 `production_task_id` / `content_id` 关联发布核验对象，不要将子计划模板 ID 当作单篇内容 ID。

### 3.2 发布结果已核验

平台真实读回成功后，调用：

```http
POST /api/content-production-lines/{line_id}/events
Content-Type: application/json
```

```json
{
  "event_type": "publish_verified",
  "source": "work_item_4",
  "actor": "work_item_4",
  "reason": "平台已读回真实发布记录",
  "evidence": {
    "platform": "xhs",
    "published_url": "https://platform.example/post/...",
    "platform_content_id": "provider-content-id",
    "verified_at": "2026-11-04T12:00:00+08:00",
    "verification_id": "provider-readback-id"
  }
}
```

约束：

1. 当前生产线必须处于 `verification_pending`。
2. `evidence` 必须非空，且必须指向真实平台读回结果；仅有用户点击“我已发布”或本地草稿状态不构成核验。
3. 成功后状态变为 `published`，并追加不可变事件记录。
4. 重复或乱序事件返回 409，不得静默覆盖。

### 3.3 发布闭环完成

T4 完成其发布后核验/复盘记录，并确认本条内容可以结束后调用：

```json
{
  "event_type": "closed",
  "source": "work_item_4",
  "actor": "work_item_4",
  "reason": "发布核验及本条复盘记录已完成",
  "evidence": {
    "verification_id": "provider-readback-id",
    "review_ref": "content_review_events:123"
  }
}
```

当前状态必须为 `published`。成功后状态变为 `closed`，唯一活动生产线约束释放，下一条内容才可以启动。

### 3.4 错误处理

- 404：生产线不存在。
- 403：发布核验/关闭事件来源不是 `work_item_4`。
- 409：当前状态不允许该事件、证据缺失或状态迁移冲突。
- 422：请求参数不合法。

T4 不得直接更新 `content_production_lines.status` 或修改 `production_context_snapshots`。必须通过事件接口或同一服务的 `ContentProductionLineStore.transition` 方法完成。

### 3.5 人工上下文豁免（T3）

在轮次已创建、任务尚未领取前，确有业务负责人确认的例外时，可调用：

```http
POST /api/production-context/waivers
Content-Type: application/json
```

```json
{
  "round_id": 123,
  "platform": "xhs",
  "waived_items": ["confirmed_business_diagnosis"],
  "reason": "业务负责人确认本轮使用已存档的线下诊断材料",
  "actor": "business-owner",
  "evidence": {
    "document_ref": "offline-diagnosis-2026-11",
    "approved_by": "business-owner"
  }
}
```

仅允许豁免当前上下文组装器明确识别的缺失事实（北极星目标、业务资料快照、已确认经营诊断、最近已发布内容的指标/复盘证据）。必须填写操作人、原因和非空证据对象；同一轮次/平台只允许一条未消费豁免。数据库表缺失、JSON 损坏或查询失败属于数据读取故障，不允许人工豁免。

豁免必须在生产任务创建前记录。任务创建时会在同一事务内校验豁免项是否覆盖全部缺失字段，并将豁免 ID 与完整原因/证据冻结进快照；豁免仅能消费一次。

## 4. 生产上下文快照结构

`production_context_snapshots.context_json` 包含：

- `snapshot_id`、`production_task_id`、`round_id`、`captured_at`
- 租户数据库/账号组作用域、当前平台账号身份
- 母计划版本、平台子计划版本、策略步骤和前置依赖
- 北极星目标及当前业务资料快照
- 已确认经营诊断报告和业务缺口
- 最近一条真实已发布内容、作品指标、审核/复盘引用
- 平台分析、账号粉丝/获赞快照
- 已确认原始素材、素材版本、素材关联、内部资料、外部引用和资源缺口
- 本次选题、模板输入和上一条复盘反馈
- 必要时的人工豁免记录

快照不可更新或删除。历史任务迁移为 `legacy_incomplete`，仅保留可追溯信息，不得伪装为新格式的完整快照。

## 5. T4 实现注意事项

1. 只有在平台读回证据真实存在时才发送 `publish_verified`。
2. 先写入 T4 自己的核验/复盘记录，再发送 `closed`。
3. 失败、待核验、已发布未关闭都不应释放下一条内容。
4. T4 不要批量预创建内容任务；每条内容结束后由 T3 的新上下文重新选择下一条。
5. 不要把本地“草稿已保存”或用户手工确认替代真实平台发布核验。
