# SPEC：图片测试组 · 四图 × 四平台跑一篇（多图配入）

> 状态：待批准
> 日期：2026-11-07
> 影响环境：服务器 `152.136.139.147`（tenant `1-1`「图片测试组」）+ 线上代码
> 红线：仅生成内容，不存草稿、不发布（R1）；不登录四平台账号

---

## 1. 背景与现状（已核实）

| 事实 | 证据 |
|---|---|
| 服务器租户「图片测试组」= `tenant_id 1-1` | `/home/ubuntu/iterwave/tenants/1-1/` |
| 4 张菜品图已上传且 `review_status=approved`、`status=active`，id = 1..4 | `tenants/1-1/data/easel.db` `media_assets` |
| 该租户 0 母计划 / 0 子计划 / 0 轮次 / 0 内容 / 0 选题 | 同上，`spiral_rounds`/`contents`/`topic_index` 均为 0 |
| 选题库 `tenants/1-1/kb/topics.csv` 只有表头 | 文件 45 字节 |
| LLM 已启用：`llm.enabled=true`、`sensenova / deepseek-flash`，`SENSENOVA_API_KEY` 已配置 | `config/config.yaml` + `config/.env`（仅确认存在，未读取值） |
| 四平台登录态均缺失（页面右上角「4 未登录」） | 浏览器只读勘察 |
| `config.yaml` 中 `platforms.kuaishou.enabled=false`，其余 3 平台启用 | `config/config.yaml` |

**核心阻碍**：素材库「配给这篇」调用 `POST /api/media/{asset_id}/use`，后端用
`UPDATE contents SET image_paths = json.dumps([单张路径])` **覆盖**原值，
因此一篇内容只能保留最后一次配的 1 张图，无法一次配入 4 张。

---

## 2. 目标

1. **能力**：素材库支持把多张已审核素材追加到同一篇内容（`contents.image_paths` 保留多张），并保持原有单图接口向后兼容。
2. **产出**：在「图片测试组」用 1 个基于这 4 张图的选题，冷启动跑 1 轮，生成 **4 篇内容（小红书 / 抖音 / 快手 / 视频号 各 1 篇）**。
3. **配图**：4 篇内容**各自配入全部 4 张图**（共 16 条使用记录）。
4. **可验证**：服务器 SQLite 中 4 篇 `contents.image_paths` 均为 4 个路径；`media_asset_usages` 共 16 行。

## 3. 非目标

- 不点「发布/发送/提交」，不存平台草稿（本轮不勾选「存草稿」）。
- 不新建母计划 / 子计划 / 生产线链。
- 不改图片生成、卡片、视觉版本逻辑。
- 不新增「删除/清空某篇配图」功能（本轮不做，列为已知限制）。
- 不修改租户历史数据（除本轮新增的选题、轮次、内容、使用记录与三项配置）。

---

## 4. 方案

### 4.1 后端：多图追加（`src/api/main.py`）

**A. 请求模型**（`MediaAssetUseRequest`，约 L1354）：

```python
class MediaAssetUseRequest(BaseModel):
    content_id: str = Field(min_length=1, max_length=128)
    usage_role: str = Field(default="illustration", pattern="^(cover|illustration|background)$")
    mode: Literal["append", "replace"] = "replace"   # 新增；默认保留旧语义
```

**B. `use_media_asset`（约 L3754）改造**：

1. 读取 `contents.image_paths`，`json.loads` 失败或非 list → 视为 `[]`。
2. 计算 `image_path` 后：
   - 路径已在列表中 → 不改写，返回
     `{"asset_id", "content_id", "platform", "image_assigned": False, "already_assigned": True, "image_count": n, "message": "该图片已在此内容中"}`。
   - `mode=replace` → 新列表 `[image_path]`（现有行为完全不变）。
   - `mode=append` 且 `usage_role=="cover"` → 插入列表首位；否则追加到末尾。
3. `UPDATE contents SET image_paths=?, visual_status='media_asset', visual_reason=NULL`。
4. `media_asset_usages` 写入前做幂等检查：
   `SELECT 1 FROM media_asset_usages WHERE media_asset_id=? AND content_id=?`，已存在则跳过（满足 R16）。
5. 响应追加 `image_count` 字段，原有字段保留。

**C. 不改动**：`GET /api/media/targets`、上传、审核、推荐、归档、`draft_saver` 多图上传路径（其已支持 `image_paths` 列表）。

### 4.2 前端（2 个文件）

**A. `web/src/api.ts`** — `useMediaAsset`（约 L2644）：
- 请求体增加 `mode: "append"`；
- 返回类型补 `already_assigned?: boolean`、`image_count?: number`。

**B. `web/src/components/MediaLibraryPane.tsx`**：
- 约 L540 说明文案由「即可替换为…」改为「即可追加为…；同一篇内容可配多张图，重复点击不会重复添加」。
- `assignToContent`（约 L257）：按 `already_assigned` 区分 toast（info：已在此内容中；success：提示当前已配 N 张）。

### 4.3 服务器现场操作（不改代码）

| 步骤 | 操作 | 说明 |
|---|---|---|
| S1 | 「内容资产 → 选题池 → 手动添加」1 条选题 | title=`门店四道招牌菜，为什么老客每次都点`；dimension=`菜品展示`；summary=`围绕门店四道主力菜品的摆盘、食材与口感做推荐，适合图文笔记` |
| S2 | 「系统设置 → 账号画像 → 定位关键词」改为 | `美食, 探店, 菜品, 餐厅, 门店`（红线词库与禁止方向保持不变）。目的：通过 `UltimateGoalChecker` 画像匹配，避免文案 WARN 反复重生成 |
| S3 | 「系统设置 → 平台与账号」启用 **快手** | 写入 `platforms.kuaishou.enabled=true`，使 4 平台均可运行 |
| S4 | 「内容生产 → ＋创建新一轮」 | 选题数 = 1；勾选 小红书/抖音/快手/视频号；**取消勾选「本轮执行存草稿」**；点「▶ 运行本轮」 |
| S5 | 等待任务结束 | 期望 4 篇内容（每平台 1 篇），轮次状态 `producing → awaiting_publish`（无草稿则为终态，按现有逻辑） |
| S6 | 「素材库」逐篇配图 | 选中内容 → 对 4 张素材各点一次「配给这篇」，4 篇 × 4 张 = 16 次 |
| S7 | 验收 | SQL 校验 + 页面截图 |

配置写入统一走现有接口：`PATCH /api/config`（S2）与平台开关的既有设置接口；若页面控件不可用，回落为同一接口的等价 `tenant_settings` 键，不直接改 `.env`。

### 4.4 部署

按 iterwave-deploy skill：rsync `src/`、`web/src/` → 服务器 `npm run build` → `systemctl restart iterwave` → `is-active` + 公网 200。无 DB 迁移。

---

## 5. 验收标准

1. `npx tsc --noEmit` 通过；服务 `active`，`http://152.136.139.147/iterwave/` 返回 200。
2. 服务器执行：
   ```sql
   SELECT id, platform, json_array_length(image_paths) FROM contents ORDER BY id;
   -- 期望 4 行，均为 4
   SELECT COUNT(*) FROM media_asset_usages;  -- 期望 16
   SELECT status FROM spiral_rounds ORDER BY id DESC LIMIT 1; -- 非 failed
   ```
3. 4 篇内容标题/正文为菜品主题，且文案画像检查未拦截。
4. 无任何发布/存草稿动作：`grep -rn "click.*发布\|click.*publish" src/` 无新增命中；本轮 `save_drafts=false`。
5. 页面截图：轮次执行结果 + 素材库某篇显示 4 张已配图。

## 6. 风险与回退

| 风险 | 处理 |
|---|---|
| 文案画像不匹配被 WARN 重生成 | S2 已把定位关键词改为菜品主题；仍失败则检查 `guard_reason` 后调整关键词重跑 |
| 4 平台同轮生成耗时较长 | 通过「全局任务队列」观察，超时先看失败步骤，不盲目重跑 |
| 追加语义影响旧用法 | `mode` 默认 `replace`，旧调用行为不变；仅前端显式传 `append` |
| 快手开启失败 | 回退为 3 平台执行并在结果中明确说明（不伪造第 4 篇） |

## 7. 已知限制（本轮不做）

- 暂无「移除单张/清空配图」入口：追加后如需删除需后续补接口。
- 素材图为 500px 级，平台端最终清晰度以后续真实存草稿/发布为准（本轮不涉及）。
