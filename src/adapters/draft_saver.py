"""
DraftSaver — 通用 Playwright 存草稿工具（四平台共用）。

参考 Easel web_publisher.py（复制进本仓库后按需取用，不是外部调用）：
- _fill_first_visible()   → 多候选选择器填写
- platform 选择器配置      → 各平台 selector 差异已抽象
- login_state.py          → 已复制到 src/vendor/easel/，cookie/token 持久化
- login_qr.py             → 已复制到 src/vendor/easel/，二维码 DOM 渲染

我们只保留 Easel 的 "finalize=draft" 分支 — 填表单 → 点"存草稿"。
**严禁走 finalize=publish 分支，R1 红线！**

平台选择器差异：
- 小红书：标题 input / 正文 contenteditable div / 封面上传 / 话题标签 / 存草稿按钮
- 抖音：标题 input / 正文 textarea / 封面上传 / 话题 @ / 存草稿按钮
- 快手：标题 input / 正文 textarea / 封面上传 / 话题 # / 存草稿按钮
- 视频号：标题 input / 正文 div / 封面上传 / 话题 # / 存草稿按钮
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, BrowserContext, async_playwright

from src.core.redact import safe_error_summary

from .base import (
    Platform, DraftResult, ContentToPublish,
    CircuitBreakerReason, FetchStatus, MetricsSnapshot,
)

logger = logging.getLogger(__name__)


# =====================================================================
# 各平台选择器配置（参考 Easel web_publisher.py）
# =====================================================================

PLATFORM_SELECTORS: dict[Platform, dict] = {
    Platform.XHS: {
        "editor_url": "https://creator.xiaohongshu.com/publish/publish",
        "title": [
            "input[placeholder*='填写标题会有更多赞哦']",
            "input.title-input",
            "input[placeholder*='标题']",
        ],
        "body": [
            "div[contenteditable='true'][class*='content']",
            "div.editor-content",
            "div[contenteditable='true']",
        ],
        "image_upload": [
            # 小红书图片上传 input 的 accept 是 .jpg,.jpeg,.png,.webp（不含 "image"）
            "input[type='file'][accept*='.jpg']",
            "input[type='file'][accept*='.png']",
            "input[type='file'][accept*='image']",
        ],
        # 图文 tab：默认落在"上传视频"，需先切到"上传图文"才出现图片 input
        "image_tab": [
            "div[class*='tab']:has-text('上传图文')",
        ],
        # P0-2 新增：上传后核验预览卡片数量
        "image_preview_selector": [
            "div.img-list div.img-container",
            "div.img-container",
            "div[class*='image-preview']",
        ],
        # P0-3 新增：平台保存成功信号
        "save_success_selector": [
            "div:has-text('保存成功')",
            "div:has-text('草稿已保存')",
            "[class*='toast-success']",
            "[class*='success-toast']",
        ],
        # P0-3 新增：保存失败信号（更快识别）
        "save_failure_selector": [
            "div:has-text('保存失败')",
            "div:has-text('上传失败')",
            "div:has-text('发布失败')",
            "[class*='error-toast']",
            "[class*='fail-toast']",
        ],
        "topic_input": [
            "input[placeholder*='添加话题']",
            "input[placeholder*='话题']",
        ],
        "save_draft_button": [
            # 2026-10 小红书改版：存草稿按钮文案从"存草稿"改为"暂存离开"
            "button:has-text('暂存离开')",
            "button:has-text('存草稿')",
            "button:has-text('保存草稿')",
            "button.save-draft",
        ],
        # R1 红线：发布按钮严禁点击
        "_publish_button_audit_only": [
            "button:has-text('发布')",
            "button:has-text('发送')",
            "button.publish-btn",
        ],
    },
    Platform.DOUYIN: {
        # 2026-10 真机：默认停在「发布视频」tab，file input 的 accept 全是 video/*；
        # 必须点「发布图文」后才会出现 accept=image/png... 的 input（多图 multiple）。
        "editor_url": "https://creator.douyin.com/creator-micro/content/upload",
        "image_tab": [
            "text=发布图文",
            "div[class*='tab']:has-text('发布图文')",
        ],
        "title": [
            "input[placeholder*='添加作品标题']",
            "input[placeholder*='添加标题']",
            "input[placeholder*='请输入标题']",
        ],
        "body": [
            "div[contenteditable='true'][class*='editor-kit-container']",
            "div[class*='editor-kit-container'] div[contenteditable='true']",
            "div[contenteditable='true']",
        ],
        "image_upload": [
            "input[type='file'][accept*='image/png']",
            "input[type='file'][accept*='image']",
        ],
        "image_preview_selector": [
            "div[class*='cover-item']",
            "div[class*='image-box']",
        ],
        "save_success_selector": [
            "div:has-text('继续编辑')",
            "div:has-text('保存成功')",
            "div:has-text('草稿已保存')",
        ],
        "save_failure_selector": [
            "div:has-text('保存失败')",
            "[class*='error-toast']",
        ],
        "topic_input": [
            "input[placeholder*='添加话题']",
            "input[placeholder*='@ 朋友']",
        ],
        # 抖音图文编辑器没有「存草稿」，底部是「发布 + 暂存离开」；暂存离开=存草稿并离开
        "save_draft_button": [
            "button:has-text('暂存离开')",
            "button:has-text('存草稿')",
            "button:has-text('保存草稿')",
        ],
        # 存草稿成功后回到上传页，顶部出现「你还有上次未发布的图文，是否继续编辑？」
        "save_success_text": ["你还有上次未发布的图文", "继续编辑"],
        # 已有旧草稿时先放弃，保证本次存的是当前内容（不清空用户其它草稿）
        "draft_prompt_discard": ["text=放弃"],
        "_publish_button_audit_only": [
            "button:has-text('发布')",
            "button.publish-btn",
        ],
    },
    Platform.KUAISHOU: {
        # 2026-10 真机：creator.kuaishou.com/content/upload 已失效（会跳 cp.kuaishou.com/profile），
        # 真实发布页是 cp.kuaishou.com/article/publish/video，上传区有「上传视频/上传图文/上传全景视频」tab。
        "editor_url": "https://cp.kuaishou.com/article/publish/video",
        "image_tab": [
            ".ant-tabs-tab-btn:has-text('上传图文')",
            "text=上传图文",
        ],
        # 快手图文的图片 input 用 set_input_files 不触发上传，必须点「上传图片」走 filechooser
        "image_upload_mode": "filechooser",
        "image_upload_trigger": [
            "text=上传图片",
            "button:has-text('上传图片')",
        ],
        "image_upload": [
            "input[type='file'][accept*='image/png']",
        ],
        # 快手图文没有独立标题框（只有描述框），标题并入正文
        "title_optional": True,
        "title": [
            "input[placeholder*='标题']",
            "input[placeholder*='输入标题']",
        ],
        "body": [
            "div[contenteditable='true'][class*='_description']",
            "div[contenteditable='true'][placeholder*='添加合适的话题和描述']",
            "div[contenteditable='true']",
        ],
        # 快手图文没有稳定的逐图预览选择器，用页面文本「N/31」核验（见 _count_uploaded_images）
        "image_preview_selector": [],
        "save_success_selector": [
            "div:has-text('还有上次未发布的图集')",
            "div:has-text('继续编辑')",
            "div:has-text('保存成功')",
        ],
        "save_failure_selector": [
            "div:has-text('保存失败')",
        ],
        # 快手图文编辑页没有「存草稿」：点「取消」离开即自动存为草稿（回到上传页显示「继续编辑」）
        "save_draft_button": [
            "[class*='_button-default']:has-text('取消')",
            "button:has-text('取消')",
            "text=取消",
        ],
        "save_success_text": ["还有上次未发布的图集", "继续编辑"],
        "draft_prompt_discard": ["text=放弃"],
        "_publish_button_audit_only": [
            "button:has-text('发布')",
        ],
    },
    Platform.WECHAT_CHANNELS: {
        # 2026-11 真机：图文入口 = 内容管理 → 图文（列表页 finderNewLifePostList）
        # 注意：直接 goto 该 URL 会被 SPA 重定向回 /platform，必须先点侧边栏「图文」再点「发表图文」。
        "editor_url": "https://channels.weixin.qq.com/platform/post/finderNewLifePostList",
        "image_flow": "wechat_finder",
        "image_create_tab": ["text=发表图文"],
        # 2026-11 实测：创作器表单在 micro/content/post/finderNewLifeCreate；
        # 外层 platform/post/finderNewLifeCreate 只是空壳。该 frame 的 DOM 对 Playwright
        # locator 不可见（count()=0），只能用 Frame.evaluate 操作。
        "image_list_frame_marker": "micro/content/post/finderNewLifePostList",
        # 真正的创作器表单 frame（该 frame 对 locator 不可见，只能用 Frame.evaluate 操作）
        "composer_frame_marker": "micro/content/post/finderNewLifeCreate",
        # 保存草稿后页面不弹 toast、URL 不变，只能去草稿箱按标题核对（实测唯一可靠信号）
        "draft_box_url": "https://channels.weixin.qq.com/platform/post/draftListManager",
        "draft_box_frame_marker": "micro/content/post/draftListManager",
        "title": [
            "input[placeholder*='填写标题']",
            "input[placeholder*='标题']",
        ],
        "body": [
            # 描述框是 div.input-editor，contenteditable 属性值为空串（不是 'true'）
            "div.input-editor[contenteditable]",
            "div[contenteditable]",
        ],
        "image_upload": [
            "input[type='file'][accept*='image']",
            "input[type='file']",
        ],
        "image_preview_selector": [],
        "save_success_selector": [
            "div:has-text('保存成功')",
            "div:has-text('草稿已保存')",
            "[class*='toast-success']",
        ],
        "save_failure_selector": [
            "div:has-text('保存失败')",
        ],
        "save_draft_button": [
            "button:has-text('保存草稿')",
            "button:has-text('存草稿')",
        ],
        "save_success_text": ["保存成功", "草稿已保存"],
        "_publish_button_audit_only": [
            "button:has-text('发表')",
            "button:has-text('发布')",
        ],
    },
}


# =====================================================================
# DraftSaver — 通用存草稿实现
# =====================================================================

class DraftSaver:
    """存草稿的 Playwright 操作封装（四平台共用）。

    R1 红线保障：
    1. 只 PLATFORM_SELECTORS[platform]["save_draft_button"] 中的选择器
    2. publish 按钮只出现在 _publish_button_audit_only 里供 grep 审计，
       永不传给 click()
    """

    def __init__(self, platform: Platform):
        self.platform = platform
        self.selectors = PLATFORM_SELECTORS[platform]

    async def fill_first_visible(
        self, page: Page, selectors: list[str], text: str,
    ) -> bool:
        """多候选选择器中找到第一个可见的填写（参考 Easel _fill_first_visible）。"""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=3000):
                    await loc.fill(text)
                    return True
            except Exception:
                continue
        logger.warning("平台 %s 没有找到可见元素: %s", self.platform, selectors)
        return False

    async def click_first_visible(
        self, page: Page, selectors: list[str], timeout: int = 5000,
    ) -> bool:
        """多候选选择器中找到第一个可见的点击。"""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=3000):
                    await loc.click()
                    return True
            except Exception:
                continue
        return False

    async def save_draft_via_page(
        self, page: Page, content: ContentToPublish,
    ) -> DraftResult:
        """在已打开的编辑器页面存草稿。

        流程：goto → 等加载 → 一次性多图上传 + 数量核验 → 填标题 → 填正文
              → 标签追加正文 → 点存草稿 → 等成功信号 → 提取平台 draft_id。

        **永不点击发布按钮**（R1）。

        成功判定（P0-3 修复）：
          ✅ 成功证据出现（toast / 页面跳转 / URL 变 / 草稿列表）→ success=True
          ⚠️ 按钮点了但无成功证据 → needs_human_verify=True（R17）
          ❌ 明确失败（登录态 / 熔断 / 图片数量不符 / 按钮未找到）→ success=False
        """
        try:
            # 0. 视频号图文走独立链路（入口在「内容管理→图文」列表页，创作器在嵌套 iframe 里）
            if content.image_paths and self.selectors.get("image_flow") == "wechat_finder":
                return await self._save_wechat_image_draft(page, content)

            # 1. 导航到编辑器
            await page.goto(self.selectors["editor_url"], wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # 2. 登录态检查
            if await self._check_login_expired(page):
                return DraftResult(
                    success=False,
                    error_msg="登录态过期",
                    login_required=True,
                )

            # 3. 检查熔断信号（R6：命中即中止本次保存，由上层上报熔断器）
            cb_signal = await self._detect_cb_signal(page)
            if cb_signal:
                logger.warning("平台 %s 检测到熔断信号: %s", self.platform, cb_signal)
                return DraftResult(
                    success=False,
                    error_msg=f"检测到熔断信号: {cb_signal.value}",
                    cb_signal=cb_signal,
                )

            # —— P0-2 修复：先校验图片路径存在性 ——
            missing = [p for p in content.image_paths if not os.path.isfile(p)]
            if missing:
                return DraftResult(
                    success=False,
                    error_msg=f"图片文件不存在: {os.path.basename(missing[0])} (共 {len(missing)} 个缺失)",
                )

            # —— 小红书/抖音/快手：默认停在视频 tab，先切到图文 tab ——
            await self._ensure_image_tab(page, bool(content.image_paths))

            # —— 已有上次未发布的草稿时先「放弃」，保证本次存的是当前内容 ——
            if content.image_paths:
                await self._discard_stale_draft(page)

            # —— 一次性多图上传（小红书/抖音 set_input_files；快手必须走 filechooser）——
            if content.image_paths:
                await self._upload_images(page, content.image_paths)

                # —— 上传后核验预览数量（图片处理需要时间，轮询等待）——
                uploaded = await self._wait_uploaded_images(
                    page, len(content.image_paths), timeout_ms=30000,
                )
                if uploaded != len(content.image_paths):
                    logger.warning(
                        "平台 %s 图片数量不符: 预期 %d / 实际 %d",
                        self.platform, len(content.image_paths), uploaded,
                    )
                    return DraftResult(
                        success=False,
                        error_msg=f"图片上传数量不符: 预期 {len(content.image_paths)} / 实际 {uploaded}",
                        uploaded_image_count=uploaded,
                    )

            # 5. 填标题（快手图文无标题框 → 标题并入正文）
            title_ok = await self.fill_first_visible(page, self.selectors["title"], content.title)
            if not title_ok:
                if not self.selectors.get("title_optional"):
                    return DraftResult(
                        success=False,
                        error_msg="标题输入框未找到",
                    )
                logger.info("平台 %s 无独立标题框，标题并入正文", self.platform)

            # —— P1-1 修复：标签追加到正文末尾（最通用方式，避免各平台标签输入框差异）——
            body_parts = [content.body]
            if content.tags:
                body_parts.append(" ".join(content.tags))
            if not title_ok:
                body_parts.insert(0, content.title)
            body_text = "\n".join(body_parts)

            # 6. 填正文（可能已追加标签）
            body_ok = await self.fill_first_visible(page, self.selectors["body"], body_text)
            if not body_ok:
                return DraftResult(
                    success=False,
                    error_msg="正文输入框未找到",
                )

            # —— P1-1 额外：如平台有独立话题输入框，也填一次 ——
            topic_sels = self.selectors.get("topic_input", [])
            if topic_sels and content.tags:
                await self.fill_first_visible(page, topic_sels, " ".join(content.tags))

            # —— 记录保存前草稿数量（小红书草稿在浏览器本地，保存后数量 +1）——
            initial_draft_count = await self._draft_count(page)

            # 7. ⚠️ 存草稿（只点 save_draft_button，严禁点 _publish_button_audit_only！）
            save_ok = await self._click_save_draft_button(page, self.selectors["save_draft_button"])
            if not save_ok:
                logger.warning("存草稿按钮未找到，尝试兜底 ...")
                save_ok = await self._fallback_save_draft(page)
            if not save_ok:
                # 小红书底部按钮在 closed shadow DOM（<xhs-publish-btn>）里，
                # Playwright 选择器不可见；调用其 save 回调（只调 _onSave，绝不碰 _onPublish）
                save_ok = await self._call_platform_save_handler(page)

            if not save_ok:
                return DraftResult(
                    success=False,
                    error_msg="存草稿按钮未找到",
                )

            # —— P0-3 修复：等待平台成功信号（最多 8s）——
            failure_evidence = await self._wait_save_failure(page, timeout_ms=1500)
            if failure_evidence:
                return DraftResult(
                    success=False,
                    error_msg=f"平台反馈保存失败: {safe_error_summary(failure_evidence, '平台未确认草稿保存成功')}",
                )

            evidence = await self._wait_save_success(
                page, timeout_ms=8000, initial_draft_count=initial_draft_count,
            )
            draft_id = await self._extract_draft_id(page)

            if evidence is None:
                # —— P0-3 核心：按钮点了但无成功证据 → needs_human_verify（R17）——
                logger.warning(
                    "平台 %s 存草稿无成功证据 content_id=%s → needs_human_verify",
                    self.platform, content.content_id,
                )
                return DraftResult(
                    success=False,
                    needs_human_verify=True,
                    error_msg="点击存草稿后未检测到平台成功信号，请人工核实",
                    platform_draft_id=draft_id,
                    platform_draft_id_missing=(draft_id is None),
                    uploaded_image_count=len(content.image_paths),
                )

            # —— 成功证据出现 ✅ ——
            logger.info(
                "✅ 平台 %s 存草稿成功 content_id=%s draft_id=%s evidence=%s",
                self.platform, content.content_id, draft_id, evidence,
            )
            return DraftResult(
                success=True,
                platform_draft_id=draft_id,
                platform_draft_id_missing=(draft_id is None),
                save_evidence=evidence,
                uploaded_image_count=len(content.image_paths),
            )

        except Exception as exc:
            logger.exception("❌ 平台 %s 存草稿失败: %s", self.platform, exc)
            return DraftResult(success=False, error_msg=safe_error_summary(exc, "平台存草稿失败，请检查登录状态后重试"))

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _check_login_expired(self, page: Page) -> bool:
        """简单检查：页面是否跳转到了登录页或出现扫码登录框。"""
        url = page.url
        if "login" in url.lower() or "signin" in url.lower():
            return True
        # 检查常见登录 prompt
        for sel in [
            "div.qrcode", "img[src*='qrcode']", "div.login-box",
            "div:has-text('扫码登录')", "div:has-text('请先登录')",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2000):
                    return True
            except Exception:
                continue
        return False

    async def _detect_cb_signal(self, page: Page) -> Optional[CircuitBreakerReason]:
        """检测页面上的熔断信号（5 种）。"""
        # 验证码
        for sel in [
            "div:has-text('验证码')", "div:has-text('CAPTCHA')",
            "img[src*='captcha']", "div.geetest_widget",
        ]:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    return CircuitBreakerReason.CAPTCHA
            except Exception:
                pass

        # 限流
        for sel in [
            "div:has-text('操作太频繁')", "div:has-text('限流')",
            "div:has-text('请稍后再试')",
        ]:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    return CircuitBreakerReason.RATE_LIMIT
            except Exception:
                pass

        # 登录过期（已在 _check_login_expired 覆盖，这里跳过）
        # 操作拦截
        # 注意：不能用宽泛的 div:has-text('违规') —— 视频号后台侧边栏有「违规管理」，
        # 祖先 div 会把整页包含进去，导致每次打开创作页都被误判成发布被拦截。
        for sel in [
            "[class*='toast']:has-text('发布被拦截')",
            "[class*='modal']:has-text('违规')",
            "[class*='dialog']:has-text('违规')",
            "[class*='toast']:has-text('违规')",
            "[role='alert']:has-text('违规')",
            "[class*='modal']:has-text('无法发布')",
            "[class*='dialog']:has-text('无法发布')",
        ]:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    return CircuitBreakerReason.ACTION_BLOCKED
            except Exception:
                pass

        # 设备验证
        for sel in [
            "div:has-text('设备验证')", "div:has-text('安全验证')",
        ]:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    return CircuitBreakerReason.DEVICE_VERIFY
            except Exception:
                pass

        return None

    async def _ensure_image_tab(self, page: Page, has_images: bool) -> None:
        """部分平台默认停在视频 tab，需要先切到图文 tab 才会出现图片上传 input。"""
        tabs = self.selectors.get("image_tab") or []
        if not has_images or not tabs:
            return
        if self.selectors.get("image_upload_mode") == "filechooser":
            # 快手：图片 input 在视频 tab 就已挂载（accept 含 png），accept 判断会误判已在图文 tab，
            # 只能用 tab 的 active 态判断。
            try:
                active = await page.evaluate(
                    """() => [...document.querySelectorAll('[class*=tab]')]
                        .some(e => (e.innerText||'').trim().includes('图文')
                                && /active/i.test(e.className || ''))"""
                )
                if active:
                    return
            except Exception:
                pass
        else:
            try:
                inp = page.locator(self.selectors["image_upload"][0])
                accept = ""
                if await inp.count() > 0:
                    accept = (await inp.first.get_attribute("accept")) or ""
                if ".jpg" in accept or ".png" in accept or "image" in accept:
                    return  # 当前已在图文 tab
            except Exception:
                pass
        if await self.click_first_visible(page, tabs, timeout=6000):
            await page.wait_for_timeout(2500)

    @staticmethod
    def _find_frame(page: Page, marker: str):
        """按 URL 子串在 page.frames 里找目标 frame；找不到返回 None。"""
        if not marker:
            return None
        return next((f for f in page.frames if marker in (f.url or "")), None)

    async def _click_wechat_nav(self, page: Page, label: str) -> bool:
        """点击视频号后台侧边栏菜单项。

        视频号是 SPA，直接 goto 列表 URL 会被重定向回 /platform（2026-11 实测），
        必须先点侧边栏「图文」/「草稿箱」再等内嵌 frame 渲染。
        """
        # 视频号侧边栏可能在 micro 子 frame 里（2026-11 实测），必须逐 frame 查找；
        # 同一个文案可能同时命中容器和叶子节点，取最后一个叶子节点点击最稳。
        script = """(label) => {
            const nodes = [...document.querySelectorAll('*')].filter(e => {
                const t = (e.innerText || '').trim();
                return t === label && e.children.length <= 2;
            });
            if (nodes.length) { nodes[nodes.length - 1].click(); return true; }
            const fuzzy = [...document.querySelectorAll('*')].filter(e => {
                const t = (e.innerText || '').trim();
                return t.startsWith(label) && e.children.length <= 2;
            });
            if (fuzzy.length) { fuzzy[fuzzy.length - 1].click(); return true; }
            return false;
        }"""
        for frame in page.frames:
            try:
                if await frame.evaluate(script, label):
                    logger.info("视频号侧边导航命中 frame: %s", (frame.url or "")[:80])
                    return True
            except Exception:  # noqa: BLE001 — 已被销毁的 frame 直接跳过
                continue
        return False

    async def _wait_frame(self, page: Page, marker: str, timeout_s: int = 15):
        """轮询等待 URL 含 marker 的 frame 出现。"""
        for _ in range(max(1, timeout_s)):
            frame = self._find_frame(page, marker)
            if frame is not None:
                return frame
            await page.wait_for_timeout(1000)
        return None

    async def _open_wechat_composer(self, page: Page) -> bool:
        """点侧边栏「图文」进入列表，并在「发表图文」出现时立刻点击。

        视频号的 micro iframe 会反复重建：先挂空壳、再渲染列表、也可能再替换一次。
        因此不能在「列表就绪」和「点击发表图文」之间留时间差，必须在同一次 evaluate
        里找到按钮并点击，否则会点到已被替换的空 frame。
        """
        marker = str(self.selectors.get("image_list_frame_marker") or "")
        clicked_nav = await self._click_wechat_nav(page, "图文")
        if not clicked_nav:
            logger.warning("视频号未找到侧边栏「图文」入口")
        for _ in range(20):
            frame = self._find_frame(page, marker)
            if frame is not None:
                try:
                    if await frame.evaluate(
                        """() => {
                            const b = [...document.querySelectorAll('button')]
                                .find(b => (b.innerText || '').trim() === '发表图文');
                            if (!b) return false;
                            b.click();
                            return true;
                        }"""
                    ):
                        return True
                except Exception:
                    pass
            await page.wait_for_timeout(1000)
        return False

    async def _wechat_composer_frame(self, page: Page, timeout_s: int = 20):
        """定位真正的图文创作器 frame（micro/content/post/finderNewLifeCreate）。

        该 frame 的 DOM 对 Playwright locator 不可见（locator.count() 返回 0），
        只能通过 Frame.evaluate 读写；frame 会被反复重建，因此每次操作前都重新查找。
        """
        marker = str(self.selectors.get("composer_frame_marker") or "")
        for _ in range(max(1, timeout_s)):
            for frame in page.frames:
                if not marker or marker not in (frame.url or ""):
                    continue
                try:
                    ready = await frame.evaluate(
                        """() => !!document.querySelector("input[placeholder*='填写标题']")
                           && !!document.querySelector("input[type='file']")"""
                    )
                except Exception:  # noqa: BLE001 — 已被销毁的 frame 直接跳过
                    continue
                if ready:
                    return frame
            await page.wait_for_timeout(1000)
        return None

    async def _wechat_inject_images(self, frame, image_paths: list[str]) -> Optional[str]:
        """在创作器 frame 内用 DataTransfer 构造 File 注入 file input。

        返回 None 表示成功，否则返回可展示的失败原因。
        """
        items = []
        for path in image_paths:
            try:
                raw = Path(path).read_bytes()
            except Exception as exc:  # noqa: BLE001 — 图片读不到要如实回报
                return safe_error_summary(exc, "读取本地图片失败")
            items.append({
                "name": Path(path).name,
                "type": mimetypes.guess_type(path)[0] or "image/png",
                "b64": base64.b64encode(raw).decode("ascii"),
            })
        try:
            res = await frame.evaluate(
                """(items) => {
                    const input = document.querySelector("input[type='file']");
                    if (!input) return { ok: false, why: 'no input' };
                    const dt = new DataTransfer();
                    for (const it of items) {
                        const bin = atob(it.b64);
                        const bytes = new Uint8Array(bin.length);
                        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                        dt.items.add(new File([bytes], it.name, { type: it.type }));
                    }
                    input.files = dt.files;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return { ok: true, count: input.files.length };
                }""",
                items,
            )
        except Exception as exc:  # noqa: BLE001
            return safe_error_summary(exc, "图片注入失败")
        if not res or not res.get("ok") or int(res.get("count") or 0) != len(image_paths):
            return f"图片注入未生效: {res}"
        return None

    async def _wechat_fill_title(self, frame, text: str) -> Optional[str]:
        """用原生 setter 写标题并回读校验；返回 None 表示成功。"""
        want = (text or "").strip()
        try:
            got = await frame.evaluate(
                """(v) => {
                    const el = document.querySelector("input[placeholder*='填写标题']");
                    if (!el) return null;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return el.value;
                }""",
                want,
            )
        except Exception as exc:  # noqa: BLE001
            return safe_error_summary(exc, "标题填入失败")
        if not got:
            return "标题框未找到或未写入"
        got = str(got).strip()
        if got == want or want.startswith(got):
            return None
        return f"标题回读不一致: {got[:20]}"

    async def _wechat_fill_body(self, frame, text: str) -> bool:
        """写图文描述（失败不阻断存草稿）。"""
        try:
            return bool(await frame.evaluate(
                """(v) => {
                    const el = document.querySelector("div.input-editor[contenteditable]")
                        || document.querySelector("div[contenteditable]");
                    if (!el) return false;
                    el.focus();
                    el.innerText = v;
                    el.dispatchEvent(new InputEvent('input', { bubbles: true }));
                    return true;
                }""",
                text,
            ))
        except Exception:  # noqa: BLE001
            return False

    async def _wechat_click_save(self, frame) -> bool:
        """点击「保存草稿」（R1：绝不点「发表」）。"""
        try:
            return bool(await frame.evaluate(
                """() => {
                    const btns = [...document.querySelectorAll('button')].filter(b => {
                        const t = (b.innerText || '').trim();
                        return (t.includes('保存草稿') || t.includes('存草稿'))
                            && !!(b.offsetWidth || b.offsetHeight);
                    });
                    if (!btns.length) return false;
                    btns[btns.length - 1].click();
                    return true;
                }"""
            ))
        except Exception:  # noqa: BLE001
            return False

    async def _save_wechat_image_draft(
        self, page: Page, content: ContentToPublish,
    ) -> DraftResult:
        """视频号图文存草稿：内容管理→图文列表 → 发表图文 → 创作器(嵌套 iframe) 填表 → 保存草稿。

        R1：只点「保存草稿」，永不点「发表」。
        """
        await page.goto(self.selectors["editor_url"], wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)

        if await self._check_login_expired(page):
            return DraftResult(success=False, error_msg="登录态过期", login_required=True)

        cb_signal = await self._detect_cb_signal(page)
        if cb_signal:
            return DraftResult(
                success=False,
                error_msg=f"检测到熔断信号: {cb_signal.value}",
                cb_signal=cb_signal,
            )

        # 直接 goto 会被重定向回首页，必须走侧边栏「图文」→「发表图文」
        if not await self._open_wechat_composer(page):
            return DraftResult(success=False, error_msg="未找到「发表图文」入口")

        # 创作器表单在 micro/content/post/finderNewLifeCreate frame 内；
        # 该 frame 的 DOM 对 Playwright locator 不可见（count()=0），只能用 evaluate 操作。
        last_err: Optional[str] = "视频号图文创作器未加载"
        for _ in range(3):
            frame = await self._wechat_composer_frame(page, timeout_s=10)
            if frame is None:
                last_err = "视频号图文创作器未加载"
                continue
            last_err = await self._wechat_inject_images(frame, content.image_paths)
            if last_err is None:
                break
            logger.warning("视频号图片注入重试失败: %s", last_err)
            await page.wait_for_timeout(2000)
        if last_err:
            return DraftResult(success=False, error_msg=f"图片上传失败: {last_err}"[:200])

        # 等平台完成上传并渲染预览
        await page.wait_for_timeout(8000)

        title_err: Optional[str] = "标题未填入"
        for _ in range(3):
            frame = await self._wechat_composer_frame(page, timeout_s=8)
            if frame is None:
                continue
            title_err = await self._wechat_fill_title(frame, content.title)
            if title_err is None:
                break
            await page.wait_for_timeout(1500)
        if title_err:
            # 标题为空时平台会拒绝保存且不报错，必须在这里中止并如实回报
            return DraftResult(
                success=False, error_msg=f"视频号标题未填入，已中止存草稿: {title_err}"[:200],
            )

        body_parts = [content.body]
        if content.tags:
            body_parts.append(" ".join(content.tags))
        body_text = "\n".join(body_parts)
        frame = await self._wechat_composer_frame(page, timeout_s=8)
        body_ok = bool(frame) and await self._wechat_fill_body(frame, body_text)
        if not body_ok:
            logger.warning("视频号描述框未填入（不影响标题与图片存草稿）")

        save_clicked = False
        for _ in range(3):
            frame = await self._wechat_composer_frame(page, timeout_s=8)
            if frame is None:
                continue
            if await self._wechat_click_save(frame):
                save_clicked = True
                break
            await page.wait_for_timeout(1500)
        if not save_clicked:
            return DraftResult(success=False, error_msg="未找到「保存草稿」按钮")

        await page.wait_for_timeout(3000)
        evidence = await self._verify_wechat_draft(page, content.title)
        if evidence:
            return DraftResult(
                success=True,
                uploaded_image_count=len(content.image_paths),
                save_evidence=evidence,
                platform_draft_id_missing=True,
            )
        return DraftResult(
            success=False,
            needs_human_verify=True,
            uploaded_image_count=len(content.image_paths),
            error_msg="已点「保存草稿」但未检测到成功信号",
        )

    async def _verify_wechat_draft(self, page: Page, title: str) -> Optional[str]:
        """视频号草稿箱按标题核对（列表在嵌套 iframe 里，逐 frame 匹配）。

        草稿箱列表是异步加载的，且 micro iframe 会重建，因此不能只扫一次：
        进到草稿箱后按秒轮询，每次重新遍历当前存活 frame 的正文。
        """
        raw = (title or "").strip()
        if not raw:
            return None
        keys = list(dict.fromkeys(raw[:n] for n in (10, 8, 6) if len(raw) >= n))
        try:
            marker = str(self.selectors.get("draft_box_frame_marker") or "")
            if self._find_frame(page, marker) is None:
                if not await self._click_wechat_nav(page, "草稿箱"):
                    return None
                if await self._wait_frame(page, marker, timeout_s=20) is None:
                    logger.warning("视频号草稿箱列表未加载")
                    return None
            for _ in range(25):
                for f in page.frames:
                    if marker and marker not in (f.url or ""):
                        continue
                    try:
                        # Frame.inner_text 对视频号这类嵌套 iframe 返回空串，evaluate 才拿得到
                        text = await f.evaluate("() => document.body.innerText")
                    except Exception:
                        continue
                    if text and any(k in text for k in keys):
                        return f"draftbox:{keys[0]}"
                await page.wait_for_timeout(1000)
        except Exception as exc:  # noqa: BLE001 — 核对失败不阻断，如实返回 None
            logger.warning("视频号草稿箱核对失败: %s", exc)
        return None

    async def _discard_stale_draft(self, page: Page) -> None:
        """平台提示「还有上次未发布的草稿，是否继续编辑？」时点「放弃」。

        只在本次确实要存新草稿时调用；点的是「放弃」而非「继续编辑」，
        避免把旧草稿的图片带进来导致重复。
        """
        selectors = self.selectors.get("draft_prompt_discard") or []
        if not selectors:
            return
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=4000)
                    await page.wait_for_timeout(1500)
                    logger.info("平台 %s 已放弃上次未发布的草稿", self.platform)
                    return
            except Exception:
                continue

    async def _upload_images(self, page: Page, image_paths: list[str]) -> None:
        """上传图片：快手必须点「上传图片」走 filechooser，其余平台直接 set_input_files。"""
        mode = self.selectors.get("image_upload_mode", "input")
        if mode == "filechooser":
            trigger = self.selectors.get("image_upload_trigger") or []
            for sel in trigger:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() == 0 or not await loc.is_visible():
                        continue
                    async with page.expect_file_chooser(timeout=15000) as fc_info:
                        await loc.click()
                    await (await fc_info.value).set_files(image_paths)
                    return
                except Exception:
                    continue
            # filechooser 全部失败 → 退回 set_input_files（快手实测不触发上传，但至少留个明确错误）
            logger.warning("平台 %s 未找到「上传图片」按钮，退回 set_input_files", self.platform)

        file_input = page.locator(self.selectors["image_upload"][0]).first
        await file_input.set_input_files(image_paths)

    async def _wait_uploaded_images(
        self, page: Page, expected: int, timeout_ms: int = 20000,
    ) -> int:
        """轮询等待图片预览数量达到预期，返回最后一次统计值。"""
        import asyncio

        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        uploaded = 0
        while True:
            uploaded = await self._count_uploaded_images(page)
            if uploaded == expected:
                return uploaded
            if asyncio.get_event_loop().time() >= deadline:
                return uploaded
            await page.wait_for_timeout(500)

    async def _call_platform_save_handler(self, page: Page) -> bool:
        """小红书底部按钮位于 closed shadow DOM，Playwright 无法选择。

        该组件在 light DOM 的 <xhs-publish-btn> 上暴露了 save 回调 `_onSave`。
        ⚠️ R1：只调用保存回调，绝不调用 `_onPublish`。
        """
        if self.platform != Platform.XHS:
            return False
        try:
            ok = await page.evaluate(
                """() => {
                    const el = document.querySelector('xhs-publish-btn');
                    if (!el || typeof el._onSave !== 'function') return false;
                    if (el.getAttribute('save-disabled') === 'true') return false;
                    const label = [
                        el.innerText, el.textContent,
                        el.getAttribute('aria-label'), el.getAttribute('title')
                    ].filter(Boolean).join(' ').replace(/\\s+/g, '');
                    if (!['暂存离开', '存草稿', '保存草稿', '保存到草稿箱', 'Save draft']
                        .some(token => label.includes(token))) return false;
                    if (['发布', '发送', '立即发布', 'Publish', 'Send']
                        .some(token => label.includes(token))) return false;
                    el._onSave();
                    return true;
                }"""
            )
            if ok:
                logger.info("平台 %s 通过 _onSave 触发存草稿（closed shadow DOM）", self.platform)
            return bool(ok)
        except Exception:
            logger.exception("平台 %s 调用 _onSave 失败", self.platform)
            return False

    async def _click_save_draft_button(
        self, page, selectors: list[str], timeout: int = 5000,
    ) -> bool:
        """仅点击文案明确表明为「保存草稿」的按钮；无法确认时失败关闭。"""
        allowed = ("暂存离开", "存草稿", "保存草稿", "保存到草稿箱", "Save draft")
        forbidden = ("发布", "发送", "立即发布", "Publish", "Send")
        for selector in selectors:
            try:
                button = page.locator(selector).first
                if not await button.is_visible(timeout=1500):
                    continue
                labels = [
                    await button.inner_text(timeout=1000),
                    await button.get_attribute("aria-label"),
                    await button.get_attribute("title"),
                ]
                label = "".join(str(value or "") for value in labels)
                compact = "".join(label.split())
                if any(token in compact for token in forbidden):
                    logger.error("平台 %s 拒绝点击疑似发布按钮 selector=%s", self.platform, selector)
                    continue
                if not any(token in compact for token in allowed):
                    logger.warning(
                        "平台 %s 保存按钮文案无法确认，按失败关闭 selector=%s",
                        self.platform, selector,
                    )
                    continue
                await button.click(timeout=timeout)
                return True
            except Exception:
                continue
        return False

    async def _fallback_save_draft(self, page: Page) -> bool:
        """严格兜底：选择器命中后仍必须通过按钮文案白名单检查。"""
        return await self._click_save_draft_button(
            page, self.selectors.get("save_draft_button", []),
        )

    async def _count_uploaded_images(self, page: Page) -> int:
        """P0-2：计算实际上传成功的图片预览卡片数量。"""
        for sel in self.selectors.get("image_preview_selector", []):
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    return count
            except Exception:
                continue
        # 兜底：平台没有稳定预览选择器时，从页面文本里读「已添加 N 张图片」/「1/31」
        import re

        try:
            text = await page.inner_text("body")
        except Exception:
            return 0
        for pattern in (r"已添加\s*(\d+)\s*张图片", r"(\d+)\s*/\s*31", r"(\d+)\s*/\s*35"):
            m = re.search(pattern, text)
            if m:
                return int(m.group(1))
        return 0

    async def _draft_count(self, page: Page) -> Optional[int]:
        """读取页面「草稿箱(n)」数量（小红书草稿存浏览器本地，保存成功即 +1）。"""
        import re

        try:
            body = await page.inner_text("body")
            m = re.search(r"草稿箱\((\d+)\)", body)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    async def _wait_save_success(
        self, page: Page, timeout_ms: int = 8000,
        initial_draft_count: Optional[int] = None,
    ) -> Optional[str]:
        """P0-3：轮询等待平台保存成功信号。

        证据优先级：成功 toast → 草稿箱数量 +1 → URL 跳转。
        超时返回 None。
        """
        import asyncio
        import re

        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            # 配置的 success selector（快查，不阻塞）
            for sel in self.selectors.get("save_success_selector", []):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        try:
                            text = await loc.inner_text() or f"toast:{sel[:40]}"
                        except Exception:
                            text = f"toast_visible:{sel[:40]}"
                        return text[:60].replace("\n", " ")
                except Exception:
                    continue

            # 小红书改版后 toast 文案在 body 文本中
            try:
                body_text = await page.inner_text("body")
            except Exception:
                body_text = ""
            # 平台约定的成功文案（抖音/快手存草稿后回到上传页会提示「继续编辑」）
            markers = ["保存成功", "草稿已保存"]
            markers.extend(self.selectors.get("save_success_text") or [])
            for marker in markers:
                if marker in body_text:
                    return f"body_text:{marker}"

            if initial_draft_count is not None:
                m = re.search(r"草稿箱\((\d+)\)", body_text)
                if m and int(m.group(1)) > initial_draft_count:
                    return f"draft_count:{m.group(1)}"

            if "draft" in page.url.lower():
                return f"url_changed:{page.url[-60:]}"

            await page.wait_for_timeout(400)

        return None

    async def _wait_save_failure(self, page: Page, timeout_ms: int = 1500) -> Optional[str]:
        """P0-3：快速检测保存失败信号，先于成功信号判断。返回失败文本或 None。"""
        for sel in self.selectors.get("save_failure_selector", []):
            try:
                loc = page.locator(sel).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                try:
                    return await loc.inner_text() or sel[:40]
                except Exception:
                    return sel[:40]
            except Exception:
                continue
        return None

    async def _extract_draft_id(self, page: Page) -> Optional[str]:
        """P0-3 增强：URL query 参数优先，DOM 兜底。"""
        import re

        # 1. URL query 参数
        try:
            url = page.url
            m = re.search(r"(?:draftId|draft_id|draft|note_id|noteId)=([A-Za-z0-9]+)", url)
            if m:
                return m.group(1)
        except Exception:
            pass

        # 2. DOM 兜底：从 toast / 跳转后的隐藏 input / meta 标签提取
        try:
            candidates = [
                "#draftId", "[name='draftId']", "[data-draft-id]",
                "meta[property='og:url']",
            ]
            for sel in candidates:
                loc = page.locator(sel).first
                try:
                    if await loc.count() > 0:
                        val = await loc.input_value() or await loc.get_attribute("content") or ""
                        if val:
                            m2 = re.search(r"([A-Za-z0-9]{8,})", val)
                            if m2:
                                return m2.group(1)
                except Exception:
                    continue
        except Exception:
            pass

        return None
