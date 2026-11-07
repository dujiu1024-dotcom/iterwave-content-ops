from __future__ import annotations

import base64
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9WQAAAAASUVORK5CYII="
)


def _serve_vite():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=WEB,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Vite dev server exited before becoming ready")
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=1):
                return process, port
        except Exception:
            time.sleep(0.25)
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=10)
    raise TimeoutError("Vite dev server did not become ready")


def _stop_vite(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def _base_content(image_count=0):
    return {
        "id": "visual-1", "round_id": 1, "topic_id": "topic-1", "platform": "xhs",
        "title": "图片任务验收", "body": "第一段内容。第二段内容。", "tags": ["验收"],
        "image_count": image_count,
        "image_paths": [f"page-{index}.png" for index in range(image_count)],
        "word_count": 20, "card_style": None, "guard_outcome": "pass",
        "guard_reason": None, "created_at": "2026-11-03T12:00:00",
        "draft_id": None, "draft_status": None, "draft_error": None,
        "draft_saved_at": None, "review_rejected": False, "review_rejection_reason": None,
    }


def _attach_browser_diagnostics(page, state):
    page.on("pageerror", lambda error: print("BROWSER_PAGE_ERROR:", error))
    page.on("console", lambda message: print("BROWSER_CONSOLE:", message.type, message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: print("BROWSER_REQUEST_FAILED:", request.url, request.failure))
    page.on("request", lambda request: state.setdefault("requests", []).append(urlparse(request.url).path))


def _mock_api(page, state):
    def fulfill(route, payload, status=200, content_type="application/json"):
        body = (
            payload if isinstance(payload, (str, bytes, bytearray))
            else json.dumps(payload, ensure_ascii=False)
        )
        route.fulfill(status=status, content_type=content_type, body=body)

    def handler(route):
        request = route.request
        path = urlparse(request.url).path
        method = request.method

        if path == "/api/auth/status":
            return fulfill(route, {"configured": True, "authenticated": True, "needs_login": False})
        if path == "/api/tenants":
            return fulfill(route, {
                "runtime_tenant_id": "default", "selected_tenant_id": "default",
                "restart_required": False,
                "items": [{
                    "id": "default", "name": "默认账号组",
                    "created_at": None, "is_active": True,
                }],
            })
        if path == "/api/status":
            return fulfill(route, {
                "enabled_platforms": ["xhs"], "platforms_without_login": [],
                "logged_in_platforms": ["xhs"], "circuit_broken_platforms": [],
                "active_rounds": 0,
            })
        if path == "/api/plans":
            plan = {
                "topic_count_per_platform": 5, "enabled_platforms": ["xhs"],
                "dimension_allocation": {}, "per_platform": {}, "cadence": {},
            }
            return fulfill(route, {
                "plan": plan, "signature": "test", "updated_at": None,
                "source": "derived", "per_platform": {}, "history": [], "stale_rounds": [],
            })
        if path == "/api/autogrow":
            return fulfill(route, {
                "config": {
                    "enabled": False, "apply_feedback": False, "topic_count": 5,
                    "cooldown_hours": 24, "max_rounds_per_day": 2,
                    "max_consecutive_failures": 3, "stop_when_goals_achieved": False,
                    "feedback_max_bias": 1, "feedback_lookback_rounds": 3,
                },
                "job_enabled": False, "next_check_at": None,
                "verdict": {"can_start": False, "action": "none", "reason": ""},
                "feedback": {
                    "source_round_ids": [], "dimension_bias": {}, "bias_reasons": {},
                    "copy_hints": {}, "dropped_hints": [],
                },
                "recent_runs": [],
            })
        if path == "/api/assets":
            return fulfill(route, {"items": [], "total": 0})
        if path == "/api/goals":
            return fulfill(route, [])
        if path == "/api/rounds":
            round_item = {
                "id": 1, "goal_id": None, "round_number": 1, "status": "completed",
                "topic_ids": "[]", "content_variants": "[]", "plan_signature": None,
                "created_at": "2026-11-03T12:00:00", "awaiting_publish_ended_at": None,
                "collecting_metrics_started_at": None,
            }
            return fulfill(route, {
                "items": [round_item], "total": 1, "page": 1, "page_size": 20, "pages": 1,
            })
        if path == "/api/rounds/1":
            return fulfill(route, {
                "id": 1, "goal_id": None, "round_number": 1, "status": "completed",
                "topic_ids": "[]", "content_variants": "[]", "plan_signature": None,
                "created_at": "2026-11-03T12:00:00", "awaiting_publish_ended_at": None,
                "collecting_metrics_started_at": None, "drafts": [],
            })
        if path == "/api/rounds/1/pipeline":
            return fulfill(route, {
                "round_id": 1, "round_number": 1, "status": "completed",
                "stages": [], "guard_rejections": 0, "drafts_by_status": {}, "per_platform": [],
            })
        if path == "/api/topics":
            return fulfill(route, {"items": [], "total": 0})
        if path == "/api/contents":
            return fulfill(route, {
                "round_id": 1, "items": [_base_content(3 if state["status"] == "completed" else 0)],
            })
        if path == "/api/contents/visual-1/visual-generation-task":
            state["status_reads"] += 1
            return fulfill(route, {
                "task_id": "manual-card-42", "status": state["status"],
                "message": state["message"], "pages": state["pages"],
                "artifact_missing_pages": [0] if state["status"] == "failed" else [],
            })
        if path == "/api/contents/visual-1/cards" and method == "POST":
            state["post_calls"] += 1
            if state.get("post_conflict"):
                return fulfill(route, {"detail": "已有图片生成任务正在执行，请勿重复点击"}, status=409)
            state["status"] = "retrying"
            state["message"] = "任务正在恢复；已完成页面会复用，未完成页面才会继续生成。"
            state["pages"] = [
                {"page_index": 0, "status": "completed", "attempts": 1, "message": ""},
                {"page_index": 1, "status": "running", "attempts": 2, "message": ""},
                {"page_index": 2, "status": "pending", "attempts": 0, "message": ""},
            ]
            return fulfill(route, {"id": "visual-1", "image_count": 3, "image_paths": []})
        if "/api/contents/visual-1/images/" in path:
            return fulfill(route, ONE_PIXEL_PNG, content_type="image/png")
        return fulfill(route, {})

    page.route("**/api/**", handler)


def test_production_visual_task_survives_reload_and_resumes_from_checkpoints():
    server, port = _serve_vite()
    screenshot_dir = ROOT / "artifacts" / "ui-viewport"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "status": "running",
        "message": "图片正在生成。刷新页面或重新进入后，任务状态会继续同步。",
        "status_reads": 0,
        "post_calls": 0,
        "pages": [
            {"page_index": 0, "status": "completed", "attempts": 1, "message": ""},
            {"page_index": 1, "status": "running", "attempts": 1, "message": ""},
            {"page_index": 2, "status": "pending", "attempts": 0, "message": ""},
        ],
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on("dialog", lambda dialog: dialog.accept("恢复图片生成测试理由"))
            _mock_api(page, state)
            _attach_browser_diagnostics(page, state)
            page.goto(f"http://127.0.0.1:{port}/?page=production&tab=review&focusRound=1&focusContent=visual-1")
            page.wait_for_timeout(1200)
            print("BROWSER_BODY:", page.locator("body").inner_text()[:2500])
            print("BROWSER_STATE:", state)
            page.get_by_text("图片生成中", exact=True).wait_for(timeout=15000)
            assert page.get_by_text("第 1 张", exact=True).count() >= 1
            assert page.get_by_text("已完成", exact=False).count() >= 1
            assert page.get_by_role("button", name="生成中…").is_disabled()

            page.reload()
            page.get_by_text("图片生成中", exact=True).wait_for(timeout=15000)
            state["status"] = "interrupted"
            state["message"] = "任务已中断。请先核实已完成页面；系统不会自动重放整轮生产任务。"
            page.get_by_text("任务已中断", exact=True).wait_for(timeout=6000)
            resume = page.get_by_role("button", name="↻ 人工核实后重试")
            assert resume.is_enabled()
            resume.click()
            page.get_by_text("正在恢复任务", exact=True).wait_for(timeout=6000)
            assert state["post_calls"] == 1
            state["status"] = "completed"
            state["message"] = "图片生成已完成，页面文件已通过完整校验。"
            state["pages"] = [
                {"page_index": index, "status": "completed", "attempts": 1, "message": ""}
                for index in range(3)
            ]
            page.get_by_text("图片生成完成", exact=True).wait_for(timeout=6000)
            # Page labels also appear in image-card download metadata, so
            # avoid locating their parent rows by the non-unique label.
            assert page.get_by_text("已完成", exact=False).count() >= 3
            # Let transient success notifications disappear before capturing layout.
            page.wait_for_timeout(4000)

            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(150)
                metrics = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth,
                    main: document.querySelector('#main-content')?.scrollWidth ?? 0,
                    mainClient: document.querySelector('#main-content')?.clientWidth ?? 0,
                    topbar: document.querySelector('.topbar')?.scrollWidth ?? 0,
                    topbarClient: document.querySelector('.topbar')?.clientWidth ?? 0
                })""")
                assert metrics["document"] <= metrics["viewport"] + 1, (
                    f"production document overflow at {width}px: {metrics}"
                )
                assert metrics["main"] <= metrics["mainClient"] + 1, (
                    f"production content overflow at {width}px: {metrics}"
                )
                assert metrics["topbar"] <= metrics["topbarClient"] + 1, (
                    f"production topbar overflow at {width}px: {metrics}"
                )
                page.screenshot(path=str(screenshot_dir / f"production-{width}.png"), full_page=True)
            browser.close()
    finally:
        _stop_vite(server)


def test_production_visual_task_shows_ocr_and_missing_file_failure():
    server, port = _serve_vite()
    state = {
        "status": "failed",
        "message": "检测到已完成页面文件缺失，已停止按成功处理。请人工核实文件后再决定是否重新生成。",
        "status_reads": 0,
        "post_calls": 0,
        "pages": [
            {"page_index": 0, "status": "missing", "attempts": 1, "message": "页面文件缺失"},
            {"page_index": 1, "status": "failed", "attempts": 3, "message": "OCR 校验未通过"},
        ],
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            _mock_api(page, state)
            page.goto(f"http://127.0.0.1:{port}/?page=production&tab=review&focusRound=1&focusContent=visual-1")
            page.get_by_text("图片生成失败", exact=True).wait_for(timeout=15000)
            assert page.get_by_text("文件缺失", exact=False).count() >= 1
            assert page.get_by_text("校验失败", exact=False).count() >= 1
            assert page.get_by_text("OCR 校验未通过", exact=False).count() >= 1
            browser.close()
    finally:
        _stop_vite(server)


def test_production_visual_task_handles_cross_worker_lease_conflict():
    server, port = _serve_vite()
    state = {
        "status": "not_found", "message": "", "status_reads": 0, "post_calls": 0,
        "post_conflict": True, "pages": [],
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on("dialog", lambda dialog: dialog.accept("跨进程冲突测试理由"))
            _mock_api(page, state)
            _attach_browser_diagnostics(page, state)
            page.goto(f"http://127.0.0.1:{port}/?page=production&tab=review&focusRound=1&focusContent=visual-1")
            button = page.get_by_role("button", name="↻ 重新生成")
            button.wait_for(timeout=15000)
            button.click()
            page.get_by_text("已有图片生成任务正在执行", exact=False).wait_for(timeout=6000)
            assert state["post_calls"] == 1
            browser.close()
    finally:
        _stop_vite(server)


def test_production_visual_task_requires_manual_check_after_timeout():
    server, port = _serve_vite()
    state = {
        "status": "failed",
        "message": "图片生成超时或服务中断。请人工核实已完成页面文件后，再安全重试；系统不会自动重放整轮生产任务。",
        "status_reads": 0,
        "post_calls": 0,
        "pages": [
            {"page_index": 0, "status": "completed", "attempts": 1, "message": ""},
            {"page_index": 1, "status": "running", "attempts": 1, "message": ""},
        ],
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            _mock_api(page, state)
            page.goto(f"http://127.0.0.1:{port}/?page=production&tab=review&focusRound=1&focusContent=visual-1")
            page.get_by_text("图片生成失败", exact=True).wait_for(timeout=15000)
            page.get_by_text("图片生成超时或服务中断", exact=False).wait_for()
            retry = page.get_by_role("button", name="↻ 人工核实后重试")
            assert retry.is_enabled()
            browser.close()
    finally:
        _stop_vite(server)



def test_dashboard_layout_has_no_horizontal_overflow_at_supported_viewports():
    """Capture the primary dashboard at desktop, tablet and mobile breakpoints."""
    server, port = _serve_vite()
    screenshot_dir = ROOT / "artifacts" / "ui-viewport"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    def handler(route):
        path = urlparse(route.request.url).path
        payload = {}
        if path == "/api/auth/status":
            payload = {"configured": True, "authenticated": True, "needs_login": False}
        elif path == "/api/tenants":
            payload = {
                "runtime_tenant_id": "default", "selected_tenant_id": "default",
                "restart_required": False,
                "items": [{"id": "default", "name": "默认账号组", "created_at": None, "is_active": True}],
            }
        elif path == "/api/status":
            payload = {
                "enabled_platforms": ["xhs"], "platforms_without_login": [],
                "logged_in_platforms": ["xhs"], "circuit_broken_platforms": [],
                "active_rounds": 0,
            }
        elif path == "/api/rounds":
            payload = {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}
        elif path == "/api/goals":
            payload = []
        elif path in {"/api/hot_topics", "/api/insights"}:
            payload = {"items": []}
        elif path == "/api/platform_progress":
            payload = {}
        elif path == "/api/todos":
            payload = {"items": [], "total": 0}
        elif path == "/api/config":
            payload = {"readonly": {"persona_keywords": [], "forbidden_directions": []}}
        elif path == "/api/goals/summary":
            payload = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page.route("**/api/**", handler)
            _attach_browser_diagnostics(page, {})
            page.goto(f"http://127.0.0.1:{port}/?page=dashboard")
            page.get_by_role("heading", name="运营总览").wait_for(timeout=15000)
            page.wait_for_timeout(300)

            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(150)
                metrics = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth,
                    body: document.body.scrollWidth,
                    main: document.querySelector('#main-content')?.scrollWidth ?? 0,
                    mainClient: document.querySelector('#main-content')?.clientWidth ?? 0,
                    topbar: document.querySelector('.topbar')?.scrollWidth ?? 0,
                    topbarClient: document.querySelector('.topbar')?.clientWidth ?? 0
                })""")
                assert metrics["document"] <= metrics["viewport"] + 1, (
                    f"horizontal overflow at {width}px: {metrics}"
                )
                assert metrics["body"] <= metrics["viewport"] + 1, (
                    f"body overflow at {width}px: {metrics}"
                )
                assert metrics["main"] <= metrics["mainClient"] + 1, (
                    f"main content overflow at {width}px: {metrics}"
                )
                assert metrics["topbar"] <= metrics["topbarClient"] + 1, (
                    f"dashboard topbar overflow at {width}px: {metrics}"
                )
                page.screenshot(path=str(screenshot_dir / f"dashboard-{width}.png"), full_page=True)
            browser.close()
    finally:
        _stop_vite(server)

 
def test_primary_pages_error_state_layout_has_no_overflow_at_supported_viewports():
    """Exercise responsive layouts for primary pages while their APIs are unavailable."""
    server, port = _serve_vite()
    screenshot_dir = ROOT / "artifacts" / "ui-viewport"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    page_routes = (
        "global-ops", "plan-center", "review", "task-queue",
        "calendar", "analytics", "agent",
    )

    def handler(route):
        path = urlparse(route.request.url).path
        if path == "/api/auth/status":
            payload = {"configured": True, "authenticated": True, "needs_login": False}
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(payload, ensure_ascii=False))
        if path == "/api/tenants":
            payload = {
                "runtime_tenant_id": "default", "selected_tenant_id": "default",
                "restart_required": False,
                "items": [{"id": "default", "name": "默认账号组",
                           "created_at": None, "is_active": True}],
            }
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(payload, ensure_ascii=False))
        if path == "/api/status":
            payload = {
                "enabled_platforms": ["xhs"], "platforms_without_login": [],
                "logged_in_platforms": ["xhs"], "circuit_broken_platforms": [],
                "active_rounds": 0,
            }
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(payload, ensure_ascii=False))
        return route.fulfill(
            status=503, content_type="application/json",
            body=json.dumps({"detail": "测试环境模拟服务暂不可用"}, ensure_ascii=False),
        )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for page_name in page_routes:
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1000}, device_scale_factor=1
                )
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.route("**/api/**", handler)
                page.goto("http://127.0.0.1:{}/?page={}".format(port, page_name))
                page.locator("#main-content").wait_for(timeout=15000)
                page.wait_for_timeout(350)

                for width in (1440, 1024, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.wait_for_timeout(100)
                    metrics = page.evaluate("""() => ({
                        viewport: document.documentElement.clientWidth,
                        document: document.documentElement.scrollWidth,
                        body: document.body.scrollWidth,
                        main: document.querySelector('#main-content')?.scrollWidth ?? 0,
                        mainClient: document.querySelector('#main-content')?.clientWidth ?? 0,
                        topbar: document.querySelector('.topbar')?.scrollWidth ?? 0,
                        topbarClient: document.querySelector('.topbar')?.clientWidth ?? 0
                    })""")
                    assert metrics["document"] <= metrics["viewport"] + 1, (
                        "{} document overflow at {}px: {}".format(page_name, width, metrics)
                    )
                    assert metrics["body"] <= metrics["viewport"] + 1, (
                        "{} body overflow at {}px: {}".format(page_name, width, metrics)
                    )
                    assert metrics["main"] <= metrics["mainClient"] + 1, (
                        "{} content overflow at {}px: {}".format(page_name, width, metrics)
                    )
                    assert metrics["topbar"] <= metrics["topbarClient"] + 1, (
                        "{} topbar overflow at {}px: {}".format(page_name, width, metrics)
                    )
                    page.screenshot(
                        path=str(screenshot_dir / "{}-error-{}.png".format(page_name, width)),
                        full_page=True,
                    )
                assert not page_errors, "{} raised browser errors: {}".format(page_name, page_errors)
                page.close()
            browser.close()
    finally:
        _stop_vite(server)



def test_review_realistic_content_layout_has_no_overflow_at_supported_viewports():
    """Render a populated review queue/detail and verify desktop/tablet/mobile layout."""
    server, port = _serve_vite()
    screenshot_dir = ROOT / "artifacts" / "ui-viewport"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-11-07T10:30:00+08:00"
    item = {
        "id": "review-layout-001", "round_id": 301, "round_number": 301,
        "round_status": "completed", "master_plan_version_id": 7,
        "master_plan_version_no": 4, "master_plan_title": "品牌长期增长母计划",
        "sub_plan_id": "subplan-xhs", "subplan_title": "小红书专业内容增长策略",
        "subplan_platform": "xhs", "subplan_version_id": 12, "subplan_version_no": 3,
        "sub_plan_stage_id": 71, "stage_order": 1,
        "strategy_step_title": "建立专业认知与案例信任",
        "strategy_step_direction": "围绕真实案例持续输出",
        "strategy_step_completion_condition": "完成 20 篇内容并复盘有效互动",
        "strategy_step_notes": "优先使用可核验案例和明确数据来源",
        "plan_attribution_status": "attributed", "topic_id": "topic-301",
        "platform": "xhs",
        "title": "如何用真实案例建立客户信任：从问题识别到方案落地",
        "body": "第一部分：识别客户面临的真实问题。第二部分：说明解决方案和验证过程。",
        "tags": ["真实案例", "客户信任"], "image_count": 0,
        "guard_outcome": "pass", "guard_reason": "事实依据完整，未发现高风险表达。",
        "review_rejected": False, "review_rejection_reason": None,
        "created_at": now, "draft_id": "draft-layout-001",
        "draft_status": "draft_saved", "draft_error": None, "saved_at": now,
        "scheduled_publish_at": None, "publish_status": "not_published",
        "published_at": None, "publish_time_source": None,
        "publish_result_note": None, "user_confirmed_at": None,
        "review_status": "pending", "review_history": [],
    }

    def handler(route):
        path = urlparse(route.request.url).path
        if path == "/api/auth/status":
            payload = {"configured": True, "authenticated": True, "needs_login": False}
        elif path == "/api/tenants":
            payload = {
                "runtime_tenant_id": "brand-main", "selected_tenant_id": "brand-main",
                "restart_required": False,
                "items": [{"id": "brand-main", "name": "主品牌运营账号组",
                           "created_at": now, "is_active": True}],
            }
        elif path == "/api/status":
            payload = {
                "enabled_platforms": ["xhs"], "platforms_without_login": [],
                "logged_in_platforms": ["xhs"], "circuit_broken_platforms": [],
                "active_rounds": 1,
            }
        elif path == "/api/review-queue":
            payload = {"items": [item], "total": 1, "page": 1, "page_size": 30, "pages": 1}
        elif path == "/api/subplans/stages/71/effect-feedback":
            payload = {
                "stage": {"id": 71, "title": "建立专业认知与案例信任"},
                "content_count": 12, "published_count": 5,
                "manual_metrics": {"samples": 2, "views": 1200, "likes": 48,
                                  "collects": 20, "comments": 6, "shares": 3},
                "automatic_metrics": {"samples": 3, "views": 5800, "likes": 210,
                                      "collects": 96, "comments": 18, "shares": 22,
                                      "new_followers": 31},
                "reviews": [{
                    "id": 8, "assessment": "effective",
                    "recommendation": "继续使用真实案例并增加可核验过程细节",
                    "evidence_note": "三篇内容获得稳定收藏和评论",
                    "confidence": "medium", "evidence_json": "{}",
                    "created_at": now,
                }],
            }
        else:
            payload = {}
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(payload, ensure_ascii=False))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/**", handler)
            page.goto(f"http://127.0.0.1:{port}/?page=review")
            page.get_by_role("heading", name="集中审核").wait_for(timeout=15000)
            page.get_by_role("heading", name=item["title"], exact=True).wait_for(timeout=15000)
            page.get_by_text("如何用真实案例建立客户信任", exact=False).wait_for(timeout=15000)
            page.get_by_text("自动采集样本：3", exact=False).wait_for(timeout=15000)

            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(150)
                metrics = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth,
                    body: document.body.scrollWidth,
                    main: document.querySelector('#main-content')?.scrollWidth ?? 0,
                    mainClient: document.querySelector('#main-content')?.clientWidth ?? 0,
                    topbar: document.querySelector('.topbar')?.scrollWidth ?? 0,
                    topbarClient: document.querySelector('.topbar')?.clientWidth ?? 0
                })""")
                assert metrics["document"] <= metrics["viewport"] + 1, (
                    f"review document overflow at {width}px: {metrics}"
                )
                assert metrics["body"] <= metrics["viewport"] + 1, (
                    f"review body overflow at {width}px: {metrics}"
                )
                assert metrics["main"] <= metrics["mainClient"] + 1, (
                    f"review content overflow at {width}px: {metrics}"
                )
                assert metrics["topbar"] <= metrics["topbarClient"] + 1, (
                    f"review topbar overflow at {width}px: {metrics}"
                )
                page.screenshot(
                    path=str(screenshot_dir / f"review-real-data-{width}.png"),
                    full_page=True,
                )
            assert not page_errors, f"review raised browser errors: {page_errors}"
            browser.close()
    finally:
        _stop_vite(server)

def test_global_operations_realistic_data_layout_has_no_overflow_at_supported_viewports():
    """Render a representative multi-tenant operations dataset at desktop/tablet/mobile widths."""
    server, port = _serve_vite()
    screenshot_dir = ROOT / "artifacts" / "ui-viewport"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-11-07T10:30:00+08:00"

    monitor = {
        "days": 30, "since": "2026-10-08T00:00:00+08:00",
        "totals": {
            "tenants": 2, "available_tenants": 2, "registered_accounts": 3,
            "registered_accounts_active": 2, "contents_period": 86, "contents_all": 420,
            "drafts_all": 120, "drafts_pending": 18, "drafts_saved": 76,
            "drafts_published": 20, "drafts_failed": 6, "media_active": 54, "media_usage": 38,
        },
        "platform_totals": {"xhs": {"contents": 62, "drafts": 80, "registered_accounts": 2},
                            "douyin": {"contents": 24, "drafts": 40, "registered_accounts": 1}},
        "tenants": [
            {
                "tenant_id": "brand-main", "tenant_name": "主品牌运营账号组（长名称布局测试）",
                "created_at": now, "db_available": True, "db_status": "available",
                "contents_period": 64, "contents_all": 300, "drafts_all": 80,
                "drafts_pending": 12, "drafts_saved": 52, "drafts_published": 14,
                "drafts_failed": 4, "media_active": 35, "media_usage": 24,
                "registered_accounts": 2, "registered_accounts_active": 2,
                "recent_execution": {"task_type": "round_pipeline", "status": "failed",
                    "has_error_detail": True, "started_at": now, "finished_at": now, "created_at": now},
                "runtime": {"available": True, "enabled_platforms": ["xhs", "douyin"],
                    "logged_in_platforms": ["xhs"], "platforms_without_login": ["douyin"],
                    "circuit_broken_platforms": [], "scheduler_running": True,
                    "scheduler_job_count": 8, "scheduler_enabled_count": 6, "active_rounds": 2},
                "platforms": {"xhs": {"contents": 48, "contents_period": 36, "drafts": 58, "registered_accounts": 1},
                              "douyin": {"contents": 16, "contents_period": 8, "drafts": 22, "registered_accounts": 1}},
            },
            {
                "tenant_id": "brand-test", "tenant_name": "区域测试账号组",
                "created_at": now, "db_available": True, "db_status": "available",
                "contents_period": 22, "contents_all": 120, "drafts_all": 40,
                "drafts_pending": 6, "drafts_saved": 24, "drafts_published": 6,
                "drafts_failed": 2, "media_active": 19, "media_usage": 14,
                "registered_accounts": 1, "registered_accounts_active": 0,
                "recent_execution": None,
                "runtime": {"available": True, "enabled_platforms": ["xhs"],
                    "logged_in_platforms": [], "platforms_without_login": ["xhs"],
                    "circuit_broken_platforms": ["xhs"], "scheduler_running": False,
                    "scheduler_job_count": 5, "scheduler_enabled_count": 0, "active_rounds": 0},
                "platforms": {"xhs": {"contents": 22, "contents_period": 22, "drafts": 40, "registered_accounts": 1}},
            },
        ],
        "account_count_note": "账号统计来自账号组数据库。",
    }
    task = {
        "queue_order": 3, "task_id": "task-layout-001", "task_type": "round_run",
        "tenant_id": "brand-main", "tenant_name": "主品牌运营账号组（长名称布局测试）",
        "aggregate_id": "round-301", "round_number": 301, "status": "running",
        "created_at": now, "available_at": now, "started_at": now, "finished_at": None,
        "attempts": 1, "error_code": None, "error_message": None,
    }
    failed_task = {**task, "queue_order": 8, "task_id": "task-layout-failed",
                   "status": "failed", "error_code": "PRODUCTION_STEP_FAILED",
                   "error_message": "测试数据中的内部异常细节不得泄漏到普通列表。"}
    todo = {
        "id": "todo-layout-001", "record_id": 501, "source": "global_queue",
        "source_label": "全局任务队列", "tenant_id": "brand-main",
        "tenant_name": "主品牌运营账号组（长名称布局测试）",
        "title": "请核实第 301 轮生产任务状态与外部副作用",
        "detail": "任务执行异常，需人工确认是否已经产生外部平台副作用。",
        "status": "pending", "created_at": now, "severity": "high",
        "target_page": "task-queue", "target_tab": None,
    }
    notification = {
        "id": 81, "level": "warning", "type": "task_failed", "platform": "xhs",
        "title": "主品牌账号组有生产任务失败待处理",
        "body": "第 301 轮生产任务需要人工核查。", "channel": "in_app",
        "sent_at": now, "read_at": None, "target_page": "task-queue",
        "target_tab": None, "target_type": "task_run", "target_id": 301,
    }
    production_monitor = {
        "master_plan": {"id": 7, "version_no": 4, "status": "active",
                        "created_at": now, "effective_at": now},
        "platforms": [{
            "id": "subplan-xhs", "platform": "xhs", "title": "小红书主品牌内容增长策略",
            "status": "active", "version_no": 3, "stage_count": 1, "round_count": 2,
            "stages": [{
                "id": 71, "title": "建立专业认知与案例信任", "status": "in_progress",
                "stage_order": 1, "stage_key": "authority", "direction": "围绕真实案例持续输出",
                "completion_condition": "完成 20 篇内容并复盘有效互动",
                "target_count": 20, "round_count": 2, "production_task_count": 3,
                "production_task_status_counts": {"succeeded": 2, "failed": 1},
                "content_count": 12, "draft_count": 9, "review_pending_count": 2,
                "review_approved_count": 7, "review_rejected_count": 1, "published_count": 5,
                "failed_draft_count": 1, "metric_record_count": 5,
                "dependencies": [], "dependencies_available": True,
                "round_status_counts": {"succeeded": 1, "failed": 1},
                "rounds": [{"id": 301, "round_number": 301, "status": "failed",
                            "created_at": now, "trigger_source": "manual"}],
                "contents": [{"id": "content-layout-001",
                    "title": "如何用真实案例建立客户信任：从问题识别到方案落地",
                    "platform": "xhs", "created_at": now, "draft_count": 2,
                    "draft_statuses": "已保存", "review_rejected": 0}],
            }],
        }],
        "totals": {"subplans": 1, "stages": 1, "rounds": 2, "successful_rounds": 1,
                   "production_tasks": 3, "contents": 12, "content_progress_percent": 60,
                   "review_pending": 2, "review_approved": 7, "review_rejected": 1,
                   "published": 5, "abnormal_rounds": 1, "failed_rounds": 1,
                   "failed_drafts": 1, "metric_records": 5},
        "effect_summary": {"views": 12800, "likes": 560, "collects": 320,
                           "comments": 48, "shares": 72, "new_followers": 95},
        "consistency_checks": [{"code": "ROUND_FAILED", "severity": "warning",
                                "count": 1, "round_id": 301,
                                "message": "有 1 个轮次需要检查失败原因。"}],
        "generated_at": now, "data_scope": "当前账号组",
        "limitations": {"production_task_source": "production_tasks",
                        "task_model_available": True, "legacy_task_run_fallback": False,
                        "metric_records_source": "metrics",
                        "published_definition": "用户确认发布"},
    }

    def handler(route):
        path = urlparse(route.request.url).path
        query = dict(part.split("=", 1) for part in urlparse(route.request.url).query.split("&") if "=" in part)
        if path == "/api/auth/status":
            payload = {"configured": True, "authenticated": True, "needs_login": False}
        elif path == "/api/tenants":
            payload = {"runtime_tenant_id": "brand-main", "selected_tenant_id": "brand-main",
                       "restart_required": False,
                       "items": [{"id": "brand-main", "name": "主品牌运营账号组",
                                  "created_at": now, "is_active": True}]}
        elif path == "/api/status":
            payload = {"enabled_platforms": ["xhs"], "platforms_without_login": [],
                       "logged_in_platforms": ["xhs"], "circuit_broken_platforms": [],
                       "active_rounds": 1, "published_samples": 5, "scoring_mode": "default",
                       "recovery": {"interrupted_tasks": 0, "requeued_data_fetch": [],
                                    "stale_rounds": [], "pending_verify": 0}}
        elif path == "/api/global-monitor":
            payload = monitor
        elif path == "/api/production-monitor":
            payload = production_monitor
        elif path == "/api/production-tasks":
            payload = {"items": [], "total": 0, "page": 1, "page_size": 100, "pages": 0}
        elif path == "/api/task-queue":
            is_failed = query.get("scope") == "failed"
            payload = {"items": [failed_task] if is_failed else [task],
                       "total": 1, "page": 1, "page_size": 12, "pages": 1,
                       "status_counts": {"queued": 2, "running": 1, "failed": 1, "interrupted": 0},
                       "tenant_status_counts": {"brand-main": {"queued": 2, "running": 1, "failed": 1}}}
        elif path == "/api/notifications":
            payload = {"items": [notification], "total": 1, "page": 1, "page_size": 5,
                       "pages": 1, "unread_total": 1}
        elif path == "/api/todos":
            payload = {"items": [todo], "total": 1,
                       "counts": {"global_queue": 1, "task_run": 0, "ops_log": 0,
                                  "circuit_breaker": 0, "hot_topic_source": 0,
                                  "media_review": 0, "notification": 0, "media_generation": 0},
                       "page": 1, "page_size": 12, "pages": 1, "capped": False}
        else:
            payload = {}
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(payload, ensure_ascii=False))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/**", handler)
            page.goto("http://127.0.0.1:{}/?page=global-ops".format(port))
            page.locator("#main-content").wait_for(timeout=15000)
            page.get_by_role("heading", name="全局运营中心").wait_for(timeout=15000)
            page.get_by_text("主品牌运营账号组（长名称布局测试）", exact=False).first.wait_for(timeout=15000)
            page.get_by_text("如何用真实案例建立客户信任：从问题识别到方案落地", exact=True).wait_for(timeout=15000)

            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(180)
                metrics = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth,
                    body: document.body.scrollWidth,
                    main: document.querySelector('#main-content')?.scrollWidth ?? 0,
                    mainClient: document.querySelector('#main-content')?.clientWidth ?? 0,
                    topbar: document.querySelector('.topbar')?.scrollWidth ?? 0,
                    topbarClient: document.querySelector('.topbar')?.clientWidth ?? 0
                })""")
                assert metrics["document"] <= metrics["viewport"] + 1, (
                    "global-ops real-data document overflow at {}px: {}".format(width, metrics)
                )
                assert metrics["body"] <= metrics["viewport"] + 1, (
                    "global-ops real-data body overflow at {}px: {}".format(width, metrics)
                )
                assert metrics["main"] <= metrics["mainClient"] + 1, (
                    "global-ops real-data content overflow at {}px: {}".format(width, metrics)
                )
                assert metrics["topbar"] <= metrics["topbarClient"] + 1, (
                    "global-ops real-data topbar overflow at {}px: {}".format(width, metrics)
                )
                page.screenshot(
                    path=str(screenshot_dir / "global-ops-real-data-{}.png".format(width)),
                    full_page=True,
                )
            assert not page_errors, "global-ops real-data raised browser errors: {}".format(page_errors)
            browser.close()
    finally:
        _stop_vite(server)


def test_agent_realistic_orchestration_data_layout_has_no_overflow_at_supported_viewports():
    """Render a populated orchestration timeline and verify desktop/tablet/mobile layout."""
    server, port = _serve_vite()
    screenshot_dir = ROOT / "artifacts" / "ui-viewport"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-11-07T10:30:00+08:00"
    orchestration = {
        "updated_at": now,
        "default": [{"id": "topic", "enabled": True}, {"id": "copy", "enabled": True}],
        "steps": [
            {"id": "topic", "enabled": True, "label": "① 选题研究", "skill": "topic-research",
             "locked": False, "requires": [], "desc": "围绕品牌长期目标筛选高相关主题。",
             "inputs": ["母计划", "经营目标"]},
            {"id": "copy", "enabled": True, "label": "② 文案生成", "skill": "copywriting",
             "locked": False, "requires": ["topic"], "desc": "生成符合账号定位的内容草稿。",
             "inputs": ["选题", "品牌资料"]},
            {"id": "quality_gate", "enabled": True, "label": "③ 内容质量门", "skill": "quality-gate",
             "locked": True, "requires": ["copy"], "desc": "检查事实依据、表达质量和风险边界。",
             "inputs": ["文案", "平台规范"]},
            {"id": "image", "enabled": True, "label": "④ 配图生成", "skill": "image-generation",
             "locked": False, "requires": ["quality_gate"], "desc": "按内容结构生成首图、内页和尾图。",
             "inputs": ["文案", "素材资产"]},
        ],
    }
    runs = [{
        "round_id": 301, "round_number": 301, "status": "partial_completed",
        "trigger_source": "manual", "created_at": now, "duration_seconds": 186,
        "step_status": {
            "topic": {"status": "success", "detail": "已筛选 8 个候选主题"},
            "copy": {"status": "success", "detail": "已生成 4 篇内容草稿"},
            "quality_gate": {"status": "failed", "detail": "有 1 篇内容缺少可核验事实依据"},
            "image": {"status": "pending", "detail": "等待质量门通过"},
        },
        "errors": ["1 篇内容需要补充事实依据后重新审核。"], "has_report": True,
    }]

    def handler(route):
        path = urlparse(route.request.url).path
        if path == "/api/auth/status":
            payload = {"configured": True, "authenticated": True, "needs_login": False}
        elif path == "/api/tenants":
            payload = {"runtime_tenant_id": "brand-main", "selected_tenant_id": "brand-main",
                       "restart_required": False,
                       "items": [{"id": "brand-main", "name": "主品牌运营账号组（长名称布局测试）",
                                  "created_at": now, "is_active": True}]}
        elif path == "/api/status":
            payload = {"enabled_platforms": ["xhs", "douyin"],
                       "platforms_without_login": ["douyin"], "logged_in_platforms": ["xhs"],
                       "circuit_broken_platforms": [], "active_rounds": 1,
                       "published_samples": 5, "scoring_mode": "default",
                       "recovery": {"interrupted_tasks": 0, "requeued_data_fetch": [],
                                    "stale_rounds": [], "pending_verify": 0}}
        elif path == "/api/orchestration":
            payload = orchestration
        elif path == "/api/orchestration/runs":
            payload = {"runs": runs}
        else:
            payload = {}
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(payload, ensure_ascii=False))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/**", handler)
            page.goto(f"http://127.0.0.1:{port}/?page=agent")
            page.get_by_role("heading", name="Agent 编排与诊断").wait_for(timeout=15000)
            assert page.locator("select option").filter(has_text="第 301 轮 · 部分完成").count() == 1
            page.locator(".agent-flow button").filter(has_text="内容质量门").click()
            page.get_by_text("有 1 篇内容缺少可核验事实依据", exact=False).wait_for(timeout=15000)

            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(150)
                metrics = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth,
                    body: document.body.scrollWidth,
                    main: document.querySelector('#main-content')?.scrollWidth ?? 0,
                    mainClient: document.querySelector('#main-content')?.clientWidth ?? 0,
                    topbar: document.querySelector('.topbar')?.scrollWidth ?? 0,
                    topbarClient: document.querySelector('.topbar')?.clientWidth ?? 0
                })""")
                assert metrics["document"] <= metrics["viewport"] + 1, (
                    f"agent document overflow at {width}px: {metrics}"
                )
                assert metrics["body"] <= metrics["viewport"] + 1, (
                    f"agent body overflow at {width}px: {metrics}"
                )
                assert metrics["main"] <= metrics["mainClient"] + 1, (
                    f"agent content overflow at {width}px: {metrics}"
                )
                assert metrics["topbar"] <= metrics["topbarClient"] + 1, (
                    f"agent topbar overflow at {width}px: {metrics}"
                )
                page.screenshot(path=str(screenshot_dir / f"agent-real-data-{width}.png"),
                               full_page=True)
            assert not page_errors, f"agent raised browser errors: {page_errors}"
            browser.close()
    finally:
        _stop_vite(server)
