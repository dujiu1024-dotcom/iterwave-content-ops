# TASKS：四图 × 四平台跑一篇（多图配入）

> 对应 spec：`specs/media-multi-attach-4platforms/spec.md`
> 执行前提：spec 已获批准

## T1 · 后端多图追加
- [x] `src/api/main.py`：`MediaAssetUseRequest` 增加 `mode: Literal["append","replace"] = "replace"`
- [x] `use_media_asset`：读旧 `image_paths` → 去重判断 → append（cover 置顶）/ replace → 写回
- [x] `media_asset_usages` 幂等：同 asset+content 不重复插入
- [x] 响应增加 `image_count`、`already_assigned`

**验证**：本地起服务或直接对函数做最小调用校验（append 两次不同图 → 2 个路径；重复同图 → 不增长）。

## T2 · 前端
- [x] `web/src/api.ts`：`useMediaAsset` 传 `mode: "append"`，返回类型补字段
- [x] `MediaLibraryPane.tsx`：说明文案与 `assignToContent` toast 区分「已在此内容中 / 已配 N 张」

**验证**：`npx tsc --noEmit`（web 目录）通过。

## T3 · 部署
- [x] rsync `src/`、`web/src/` 到 `/home/ubuntu/iterwave/`（仅改动的 3 个文件，避免带出未部署的 P0/P1 改动）
- [x] 服务器 `npm run build` → `systemctl restart iterwave`
- [x] `systemctl is-active iterwave` = active；公网 `/iterwave/` 200

## T4 · 现场配置（服务器租户 1-1）
- [x] 选题池手动添加 1 条菜品选题（title/dimension/summary 见 spec S1）
- [x] 账号画像定位关键词改为 `美食, 探店, 菜品, 餐厅, 门店`（快手样稿追加 `家常菜, 招牌菜` 才过画像门）
- [x] 平台与账号启用快手（S3）
- [x] 校验：`GET /api/topics` 出现新选题；`GET /api/status` 返回 4 个启用平台

## T5 · 运行轮次
- [x] 内容生产：新建轮次 round 3（created）
- [x] 生成 4 篇内容：轮次执行路径在 AI 卡片 OCR 硬门（sensenova-u1.5-fast 连续 3 次不达标）失败；改用平台「快速体验样稿导入正式生产」通道（该通道明确不自动出图），4 平台各导入 1 篇，round 3 下共 4 篇内容
- [x] 失败原因已记录：round 1（未启用 AI 配图）、round 2（AI 卡片 OCR 未通过），均未产生内容

## T6 · 配图
- [x] 4 篇内容各配入 4 张图（16 次，`mode=append`）
- [x] 接口返回 `image_assigned=true`，无报错

## T7 · 验收
- [x] SQL：4 篇 contents 的 `json_array_length(image_paths)=4`
- [x] SQL：`media_asset_usages` 共 16 行
- [x] 轮次状态：round 3 = created（内容已导入，未执行出图）；contents 4 篇均 `visual_status=media_asset`
- [x] 页面截图：内容生产第 3 轮显示 4 篇待保存草稿（四平台各 1）+ 素材库 4 张缩略图正常加载
- [x] 复核红线：无发布/存草稿调用，4 个草稿均为 `draft_pending`（无 `draft_saved`）

## T8 · 收尾
- [x] git 提交：用户未要求，未提交
- [x] 向用户汇报：平台清单、4 篇标题、配图结果、已知限制（无移除配图入口、未存草稿、AI 卡片 OCR 门未通过）
