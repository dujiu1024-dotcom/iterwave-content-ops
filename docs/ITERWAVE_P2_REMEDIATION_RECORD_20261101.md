# IterWave P2 修复记录（2026-11-01）

## 范围

本记录对应 PR #49 修复单中的 P2：后台任务运行时读取配置必须绑定任务所属账号组，避免图片生成、图片质量评分和指标归一化误用前台活动账号组的全局配置。

## 实施内容

| 项目 | 处理结果 | 代码位置 |
|---|---|---|
| 任务配置上下文 | 新增 `task_context.bind_config/current_config`，使用 ContextVar 绑定并在退出时恢复 | `src/core/task_context.py` |
| Worker 租户绑定 | 每条后台任务执行时绑定其 tenant_id 对应的 cfg，任务结束后恢复上下文 | `src/core/task_worker.py` |
| 图片生成 | 首次核查发现主分支实现与本记录不一致；已在修复分支新增 _resolve_config()，优先读取显式 cfg、再读取当前任务 cfg、最后回退全局配置，等待 CI | `src/core/media_generation.py` |
| 图片质量评分 | 未显式传入 cfg 时，先读取当前任务 cfg，再回退到全局配置 | `src/core/media_quality.py` |
| 指标归一化 | 未显式传入 cfg 时，先读取当前任务 cfg，再回退到全局配置 | `src/core/metrics/__init__.py` |

## 回归测试

修改 `test_worker_executes_queued_scheduler_job_in_its_tenant_context`，除验证登录态目录隔离外，同时断言任务执行期间 `current_config()` 返回任务所属账号组的 cfg 对象。

## 验证

- 基础整改 PR #85 已合并；CI run `41307905631`：后端 **578 passed / 2 warnings**，前端构建、Python compile、安全边界 lint 全部通过。
- 主分支核查发现 generate_image_bytes() 曾直接读取全局配置，与本记录原描述不一致；修复已提交至 fix/tenant-image-generation-config-20261101。新增测试覆盖绑定租户配置和显式配置优先级。
- 本 P2 增量需在新 PR 的 CI 中重新验证；此前基于旧基础分支的 CI 结果不能替代本次验证。

## 部署验收边界

CI 不等于生产部署。需要在多账号组环境中验证：后台任务在 A 组入队后，即使前台切换到 B/C 组，图片生成、质量评分、指标归一化仍使用 A 组配置。不得将真实平台登录态或外部请求结果伪装为已验证。
