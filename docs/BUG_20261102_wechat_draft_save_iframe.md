# BUG 记录：视频号存草稿一直失败（2026-11-02）

## 现象

- 前端「内容生产 → 生产线」第 2 / 3 / 4 轮均在最后一步失败，提示：
  `已点「保存草稿」但未检测到成功信号`（draft.status = failed, needs_human_verify）。
- 平台侧草稿箱数量没有变化：一直是 `草稿箱 (10)`，最新一条时间为 12:58；
  第 3 轮（14:07）、第 4 轮（14:13）生成的内容都没有出现在草稿箱。
- 结论：不是"检测假阴性"，而是**平台根本没有收到图片和标题**，保存动作是空操作。

## 根因

视频号图文创作器的真正表单在 iframe：

`https://channels.weixin.qq.com/micro/content/post/finderNewLifeCreate`

这个 frame 对 Playwright 的 locator 完全不可见（但 `Frame.evaluate` 可以读到 DOM）：

| 调用 | 结果 |
|---|---|
| `frame.evaluate(() => document.querySelector("input[type='file']").outerHTML)` | 能拿到 `<input type="file" accept="image/*" multiple style="display:none">` |
| `frame.locator("input[type='file']").count()` | `0` |
| `frame.wait_for_selector("input[type='file']")` | 5s 超时 |
| `frame.locator(...).set_input_files(...)` | 20s 超时 |

因此旧实现（依赖 locator）实际是：

1. `_wait_frame_with(page, "finderNewLifeCreate", upload_sel)` 命中了外层空壳 frame
   `platform/post/finderNewLifeCreate`；
2. `set_input_files` 打到空壳 frame 的隐藏 input → 平台无感知；
3. 标题 `locator.fill` 也写在空壳 frame；
4. 「保存草稿」点击没有真正落到真实创作器 → 平台不保存，也不报错；
5. 后续草稿箱核对自然找不到标题。

## 修复方案

视频号创作器交互全部改为 **frame 定位 + `frame.evaluate`**（不再使用 locator）：

1. 用 URL 标记 `micro/content/post/finderNewLifeCreate` 定位真实创作器 frame；
2. 图片上传：读取本地图片 → base64 → 在 frame 内用 `DataTransfer` 构造 `File`，
   赋给 `input.files` 并派发 `change` 事件；
3. 标题：用原生 `value` setter 赋值并派发 `input` / `change`；
4. 正文：写 `div[contenteditable]` 并派发 `input`；
5. 保存：`document.querySelectorAll('button')` 里找「保存草稿」并 `click()`；
6. 保存后仍以「草稿箱按标题核对」作为成功证据（轮询，最多 25s）。

验证过的诊断结果（修复前）：

```
frame: .../micro/content/post/finderNewLifeCreate
locator count: 0
frame.set_input_files err: Timeout 12000ms exceeded
DataTransfer 注入: {'ok': True, 'count': 3}
imgs: 3   # 预览出现「所选图片」，说明注入生效
```

## 重测计划

1. 通过前端「内容生产 → 生产线」新建一轮、选题数 = 1；
2. 观察阶段计数：I 存草稿成功应为 1；
3. 平台草稿箱数量应从 10 增加到 11，并能按标题检索到本轮内容。
