# 视觉生产运行规范

## 平台输出规格

`src/core/visual_specs.py` 是唯一平台画布规格入口。当前四个平台统一使用 1080×1440 PNG（3:4）作为保守通用基线，最多 9 页；平台实测或官方规格确认后，只更新该配置和对应测试，不在渲染器内散落尺寸常量。该基线不是对平台上传限制的保证。

## 风格 / 字体 / 组件样式：单一来源

- `src/core/visual_catalog.json` 是可选风格（含调色板）、字体、组件样式的唯一数据来源。
- 渲染器、轮次生产、手动重出图、品牌配置校验都通过 `src/core/visual_catalog.py` 读取；前端 `VisualBrandPane` 通过 `GET /api/visual/catalog` 获取选项（含本机字体是否安装），前端不写死任何选项。
- 新增风格只改 JSON（并跑 `tests/test_visual_*`），不改代码。

## 字体与授权

- 生产渲染默认使用 Noto Sans CJK SC。
- CI 安装 Debian/Ubuntu `fonts-noto-cjk` 包；生产 Linux 主机可运行 `bash scripts/install_visual_fonts.sh`。
- Noto CJK 字体按 SIL Open Font License 1.1 发布；商业使用需保留字体包自带的 OFL 与版权声明。不得把字体文件从不明来源复制进仓库。
- 可选字体见 catalog：`Noto Sans CJK SC`、`Source Han Sans SC`、`Microsoft YaHei`（后者依赖部署操作系统合法提供，不随项目分发）。中文卡片不再提供纯西文字体。
- **缺字体直接报错，不静默回退**：渲染前用 fontconfig（`fc-match`）确认实际匹配到所请求字体，渲染时再在浏览器内探测字体度量；任一失败都抛错。未安装的字体在配置页不可选。

## 排版与硬门（确定性）

- 分页：在 Chromium 中逐段实测高度做贪心分页，单段过长时按句切分；超过页数上限直接报错（「不允许静默截断」），并校验分页后的文字与正文逐字一致。
- 版式硬门：溢出、越界、元素重叠任一出现即阻止输出。
- 网络隔离：渲染使用独立无持久化的浏览器上下文，拦截一切非 `data:`/`about:` 请求；页面若尝试外联即报错。
- OCR 硬门：按 DOM 文本区域（标题、步骤标签、每个信息组件段落）分区识别——按 DOM 报告的文字颜色二值化（浅色字自动变为白底黑字）、缩放到目标字高后再识别；每个数字 token 单独框选、白名单识别，数字必须逐位一致。阈值：字符召回 ≥0.86、顺序相似度 ≥0.72、数字零缺失。OCR 不可用时状态为 `unavailable`，绝不视为通过。
- AI 审美分只作参考（软门），不替代硬门。

## AI 配图（与主干统一）

- 配置唯一来源：`media_generation_settings`（图片素材库设置：启用开关、模型、尺寸、风格前缀），读取函数 `content_pipeline.image_generation_settings`。不再使用 `ITERWAVE_ENABLE_AI_VISUALS` 等环境变量开关。
- 轮次生产：封面底图 + 按实测分页逐页生成正文底图，失败的页退回主题底色；文字始终由 Chromium 排版叠加。
- `contents.visual_status` 沿用 `generated`（含 AI 底图）/ `degraded`（纯排版叠字）/ `media_asset`，不新增取值。
- 调用外部图像服务前必须结束 SQLite 写事务（手动重出图先 `commit`；单页重做先单独提交任务登记再调模型）。

## 单页重做

- 仅支持该页保留了原始 AI 底图的视觉版本，且图片素材库设置已启用 AI 配图。
- 单页重做只替换指定页，其他页沿用原文件；创建新视觉版本并保留历史版本。
- 新版本状态为 `review_required`，仍需人工审核，不会自动发布。

## 租户品牌配置与信息图组件

账号组品牌配置可以覆盖风格预设的主色/辅助色/背景色/正文色、字体与组件样式（`card`/`minimal`/`editorial`）。渲染器在不改写原文的前提下把段落映射为语义组件：

- 「第一步 / 第三件事」等序号标题与 `1.`、`2、` 有序列表 → 步骤卡（序号徽标 + 首个指标数字放大）；
- 含数字的短句 → 指标强调；
- 引号开头 → 引用面板；
- 其余 → 文本块。


## 原始素材真实消费审计（WP5）

任务输入中的 `original_assets` 是不可变候选/授权上下文，`visual_asset_selection.selected_by_role` 是筛选结果；两者都不代表素材已被图像模型消费。

只有 `generate_image_bytes` 收到 Provider 返回的有效图片结果、并通过目标尺寸校验后，才会触发消费审计回调。回调记录实际成功的 Provider/模型、原始素材版本及 SHA-256、生成结果 SHA-256、页面角色/索引和幂等事件 ID。Provider 请求失败、超时或拒绝请求不会产生 consumed 记录；OCR/整组质量门禁随后失败，不会抹去已经真实发生的 Provider 消费事实。

WP5 在既有 `production_task_asset_usages` 中追加 `usage_role=consumed:...` 事件行，不更新原有授权快照，也不创建平行素材库/任务状态表。迁移 169 增加 Provider、模型、输入/输出哈希、事件 ID 和消费时间字段；事件 ID 唯一索引保证相同生成结果重试时幂等。消费回调使用 `production_task_id`，与视觉任务缓存 ID 分离。

任务素材审计接口 `GET /api/production-tasks/{task_id}/assets` 同时返回授权快照行和 consumed 事件行。消费行可通过页面角色、Provider/模型、输入/输出哈希及 `generation_event_id` 追溯。


## 快速体验样稿与素材库人工关联边界

快速体验样稿的 `/api/activation/saved/{sample_content_id}/promote` 只负责将已通过正文质量门的文案导入正式轮次，并创建待存草稿。它不会创建 `production_tasks`、生成图片、保存平台草稿或发布；导入后的内容初始视觉状态为 `degraded`，必须另行进入正式视觉生产流程。

素材库的 `POST /api/media/{asset_id}/use` 是人工图片关联操作：它将已审核素材的原图路径写入 `contents.image_paths`，并追加 `media_asset_usages` 关联行。该记录只表示人工选择/关联，不表示图片被 AI Provider 消费，不得映射为 `production_task_asset_usages` 的 consumed 事件。

正式参考图再创作必须经过原始素材登记与版本快照、权限/隐私/质量确认、任务关联、VisualPlan、真实图片字节 Provider 输入、整组 OCR/质量门禁及 Provider 成功后的消费审计。若轮次缺少不可变计划/阶段/生产线绑定，必须先修复轮次归属并重新创建符合约束的正式生产任务；不得伪造计划归属或绕过 `ProductionTaskStore` 的绑定校验。

产品交互应将“从素材库选择原图”与“AI 参考图再创作”明确区分。前者保持 `visual_status=media_asset`；后者只有在视觉生产门禁通过后才能标记为生成成品。
