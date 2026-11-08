from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
ARTIFACTS = ROOT / "test-results" / "responsive"
VIEWPORTS = (1440, 1024, 768, 390)
ROUTES = (
    "dashboard", "activation", "global-ops", "hot", "goals", "plan-center",
    "business-profile", "production", "review", "calendar", "assets",
    "operations", "analytics", "gaps", "agent", "accounts", "onboarding",
    "settings", "task-queue", "business-diagnosis",
)
# Capture all routable pages at every required viewport.
# API fixtures fail closed, so screenshots validate layout without fabricating data.
SCREENSHOT_ROUTES = set(ROUTES)


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


def _install_api_fixtures(page):
    def handle(route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if path.endswith("/api/auth/status"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body='{"configured":true,"authenticated":true,"needs_login":false}',
            )
        if path.endswith("/api/status"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body='{"enabled_platforms":[],"logged_in_platforms":[],"platforms_without_login":[],"circuit_broken_platforms":[],"active_rounds":0,"published_samples":0,"scoring_mode":"test","recovery":{"interrupted_tasks":0,"requeued_data_fetch":[],"stale_rounds":[],"pending_verify":0}}',
            )
        if path.endswith("/api/tenants"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body='{"runtime_tenant_id":"default","selected_tenant_id":"default","restart_required":false,"items":[{"id":"default","name":"Viewport test","created_at":null,"is_active":true}]}',
            )
        if path.endswith("/api/schedule") and route.request.method == "GET":
            params = parse_qs(urlparse(route.request.url).query)
            start_date = params.get("start_date", ["2026-11-08"])[0]
            end_date = params.get("end_date", [start_date])[0]
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "start_date": start_date, "end_date": end_date,
                    "items": [], "total": 0, "unscheduled": [],
                    "unscheduled_total": 0, "unscheduled_page": 1,
                    "unscheduled_page_size": 50,
                }),
            )
        if path.endswith("/api/production-batches/987654321"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "batch": {
                        "round_id": 987654321, "round_number": None,
                        "round_status": "completed", "created_at": "2026-11-08T10:00:00+08:00",
                        "master_plan_version_id": 99001, "sub_plan_id": "internal-sub-plan-id",
                        "sub_plan_title": None, "platform": "xhs",
                        "sub_plan_version_id": 99002,
                        "strategy_step_id": "internal-strategy-step-id",
                        "strategy_step_title": "内容策略阶段",
                        "production_line_version_id": 99003,
                        "attribution_status": "manual_review",
                    },
                    "tasks": [],
                }),
            )
        if path.endswith("/api/production-batches"):
            params = parse_qs(urlparse(route.request.url).query)
            start_date = params.get("start_date", ["2026-11-08"])[0]
            batch = {
                "round_id": 987654321, "round_number": None,
                "round_status": "completed", "created_at": f"{start_date}T10:00:00+08:00",
                "master_plan_version_id": 99001, "sub_plan_id": "internal-sub-plan-id",
                "sub_plan_title": None, "platform": "xhs",
                "sub_plan_version_id": 99002,
                "strategy_step_id": "internal-strategy-step-id",
                "strategy_step_title": "内容策略阶段",
                "production_line_version_id": 99003,
                "attribution_status": "manual_review",
                "task_count": 0, "pending_task_count": 0, "running_task_count": 0,
                "succeeded_task_count": 0, "failed_task_count": 0, "cancelled_task_count": 0,
            }
            return route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"items": [batch], "total": 1}),
            )
        if path.endswith("/api/content-plans/viewport-plan-1"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "id": "viewport-plan-1",
                    "title": "视觉验收计划：用于验证长标题与详情弹窗的窄屏换行",
                    "topic": "测试主题：" + "长文本主题片段。" * 18,
                    "platforms": ["xhs"], "planned_count": 3,
                    "scheduled_date": "2026-11-08", "status": "planned",
                    "notes": "测试备注：" + "长文本备注片段。" * 24,
                    "version": 1, "created_at": "2026-11-08T10:00:00+08:00",
                    "updated_at": "2026-11-08T10:00:00+08:00",
                    "drafts": [{
                        "draft_id": "internal-draft-id-should-not-show",
                        "platform": "xhs", "status": "draft_pending",
                        "title": "", "round_id": None, "round_number": None,
                    }],
                    "rounds": [{
                        "round_id": 987654321, "round_number": None,
                        "task_id": "internal-task-id-should-not-show",
                        "created_at": "2026-11-08T10:00:00+08:00",
                        "strategy_step_id": "internal-strategy-step-id",
                        "production_line_version_id": 99003,
                        "attribution_status": "manual_review",
                    }],
                }),
            )
        if path.endswith("/api/content-plans"):
            params = parse_qs(urlparse(route.request.url).query)
            start_date = params.get("start_date", ["2026-11-08"])[0]
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "items": [{
                        "id": "viewport-plan-1",
                        "title": "视觉验收计划：用于验证长标题与详情弹窗的窄屏换行",
                        "topic": "测试主题",
                        "platforms": ["xhs"], "planned_count": 3,
                        "scheduled_date": start_date, "status": "planned",
                        "notes": "测试备注", "version": 1,
                        "created_at": "2026-11-08T10:00:00+08:00",
                        "updated_at": "2026-11-08T10:00:00+08:00",
                    }],
                    "total": 1,
                }),
            )
        if path.endswith("/api/drafts"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body='{"items":[{"id":"viewport-draft-1","platform":"xhs","title":"发布确认控件布局测试","status":"draft_pending","guard_confirmation_allowed":false,"guard_outcome":"needs_review","guard_reason":"测试数据：需要人工复核","review_rejected":false,"platform_content_sync":"matched"}],"total":1,"page":1,"pages":1,"page_size":100}',
            )
        # Fail closed for business APIs. Each page should render its normal
        # empty/error state rather than treating an invented success payload as real data.
        return route.fulfill(
            status=503,
            content_type="application/json",
            body='{"detail":"Responsive viewport fixture: business API intentionally unavailable"}',
        )

    page.route("**/api/**", handle)


def test_all_routable_pages_at_required_viewports():
    server, port = _serve_vite()
    failures = []
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in VIEWPORTS:
                page = browser.new_page(
                    viewport={"width": width, "height": 900 if width > 390 else 844},
                    device_scale_factor=1,
                )
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                _install_api_fixtures(page)

                for route in ROUTES:
                    page_errors.clear()
                    page.goto(
                        f"http://127.0.0.1:{port}/?page={route}",
                        wait_until="domcontentloaded",
                    )
                    page.locator("#main-content").wait_for(state="visible", timeout=10000)
                    page.wait_for_timeout(250)

                    result = page.evaluate(
                        """() => {
                          const visible = (element) => {
                            const style = getComputedStyle(element);
                            const rect = element.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden'
                              && rect.width > 0 && rect.height > 0;
                          };
                          const overflowingControls = [...document.querySelectorAll(
                            'button, input, select, textarea'
                          )].filter(visible).map((element) => {
                            const rect = element.getBoundingClientRect();
                            let ancestor = element.parentElement;
                            let insideHorizontalScroller = false;
                            while (ancestor && ancestor !== document.body) {
                              const overflowX = getComputedStyle(ancestor).overflowX;
                              if (['auto', 'scroll'].includes(overflowX)
                                && ancestor.scrollWidth > ancestor.clientWidth + 1) {
                                insideHorizontalScroller = true;
                                break;
                              }
                              ancestor = ancestor.parentElement;
                            }
                            return {
                              tag: element.tagName.toLowerCase(),
                              label: element.getAttribute('aria-label')
                                || element.textContent?.trim().slice(0, 48)
                                || element.getAttribute('placeholder') || '',
                              left: Math.round(rect.left),
                              right: Math.round(rect.right),
                              width: Math.round(rect.width),
                              insideHorizontalScroller,
                            };
                          }).filter((item) => !item.insideHorizontalScroller
                            && (item.left < -1 || item.right > innerWidth + 1));
                          const main = document.querySelector('#main-content');
                          const topbar = document.querySelector('.topbar');
                          return {
                            viewport: innerWidth,
                            documentWidth: document.documentElement.scrollWidth,
                            bodyWidth: document.body.scrollWidth,
                            mainRight: main ? Math.round(main.getBoundingClientRect().right) : null,
                            topbarRight: topbar ? Math.round(topbar.getBoundingClientRect().right) : null,
                            topbarHeight: topbar ? Math.round(topbar.getBoundingClientRect().height) : null,
                            renderFailure: document.body.innerText.includes('页面渲染出错'),
                            visibleControlsOutsideViewport: overflowingControls.slice(0, 12),
                          };
                        }"""
                    )

                    if result["documentWidth"] > width + 1 or result["bodyWidth"] > width + 1:
                        failures.append(
                            f"{route}@{width}: horizontal document overflow "
                            f"(document={result['documentWidth']}, body={result['bodyWidth']})"
                        )
                    if route == "task-queue":
                        filters = page.locator(".task-queue-filters")
                        if filters.count() != 1 or not filters.is_visible():
                            failures.append(f"{route}@{width}: compact history filters are missing")
                        else:
                            filter_layout = filters.evaluate(
                                """element => {
                                  const controls = [...element.children].filter(control =>
                                    control.matches('select, input')
                                  );
                                  const style = getComputedStyle(element);
                                  const tops = controls.map(control =>
                                    control.getBoundingClientRect().top
                                  ).sort((a, b) => a - b);
                                  let rows = 0;
                                  let previousTop = null;
                                  for (const top of tops) {
                                    if (previousTop === null || top - previousTop > 6) rows += 1;
                                    previousTop = top;
                                  }
                                  return {
                                    rows,
                                    display: style.display,
                                    columns: style.gridTemplateColumns,
                                    width: Math.round(element.getBoundingClientRect().width),
                                    controls: controls.map(control => ({
                                      tag: control.tagName,
                                      top: Math.round(control.getBoundingClientRect().top),
                                      width: Math.round(control.getBoundingClientRect().width)
                                    }))
                                  };
                                }"""
                            )
                            filter_rows = filter_layout["rows"]
                            expected_rows = 1 if width > 1100 else 2 if width > 680 else 3
                            if filter_rows != expected_rows:
                                failures.append(
                                    f"{route}@{width}: history filters use {filter_rows} rows "
                                    f"(expected {expected_rows}; layout={filter_layout})"
                                )

                        # Render a deterministic task-row fixture so responsive row CSS is
                        # exercised even when business APIs intentionally fail closed.
                        row_layout = page.evaluate(
                            """() => {
                              const host = document.querySelector('#main-content');
                              const row = document.createElement('div');
                              row.className = 'panel task-queue-row';
                              row.innerHTML = `
                                <div class="task-queue-main"><strong>内容生产任务</strong><div>测试账号组 · 运营内容汇总</div></div>
                                <div class="task-queue-status"><span class="status-pill producing">执行中</span></div>
                                <div class="task-queue-time">2026-11-08 13:00</div>
                                <div class="task-queue-execution">开始：2026-11-08 13:01<div>耗时：12.3 秒</div></div>
                                <div class="task-queue-actions"><button class="btn sm">查看详情</button><button class="btn sm ghost">取消任务</button></div>
                              `;
                              host.appendChild(row);
                              const style = getComputedStyle(row);
                              const children = [...row.children].map(element => {
                                const rect = element.getBoundingClientRect();
                                return {className: element.className, left: Math.round(rect.left),
                                  right: Math.round(rect.right), top: Math.round(rect.top),
                                  width: Math.round(rect.width), height: Math.round(rect.height)};
                              });
                              return {columns: style.gridTemplateColumns.split(' ').length,
                                documentWidth: document.documentElement.scrollWidth,
                                bodyWidth: document.body.scrollWidth,
                                rowRight: Math.round(row.getBoundingClientRect().right), children};
                            }
                            """
                        )
                        expected_columns = 5 if width > 1100 else 2 if width > 680 else 1
                        if row_layout['columns'] != expected_columns:
                            failures.append(f"{route}@{width}: task row has {row_layout['columns']} columns (expected {expected_columns}; layout={row_layout})")
                        if row_layout['documentWidth'] > width + 1 or row_layout['bodyWidth'] > width + 1:
                            failures.append(f"{route}@{width}: task row fixture causes horizontal overflow: {row_layout}")
                        if row_layout['rowRight'] > width + 1:
                            failures.append(f"{route}@{width}: task row exceeds viewport: {row_layout}")
                        if any(item['width'] < 1 or item['left'] < -1 or item['right'] > width + 1 for item in row_layout['children']):
                            failures.append(f"{route}@{width}: task row child is clipped or outside viewport: {row_layout}")

                        # Capture the row itself, not only the page's empty/error-state screenshot.
                        # The fixture is appended below the rendered content and must be scrolled
                        # into view so reviewers can inspect the actual responsive arrangement.
                        row_locator = page.locator("#main-content .task-queue-row")
                        row_locator.scroll_into_view_if_needed()
                        page.screenshot(
                            path=str(ARTIFACTS / f"task-queue-row-{width}.png"),
                            full_page=False,
                            animations="disabled",
                        )

                    if route == "plan-center" and width <= 760:
                        hierarchy = page.locator(".plan-hierarchy")
                        hint = page.locator(".plan-hierarchy-hint")
                        if hierarchy.count() != 1 or hierarchy.get_attribute("aria-label") is None:
                            failures.append(f"{route}@{width}: plan hierarchy is not exposed as an accessible scroll region")
                        if hint.count() != 1 or not hint.is_visible():
                            failures.append(f"{route}@{width}: mobile plan hierarchy scroll hint is not visible")
                        elif "左右滑动" not in (hint.inner_text() or ""):
                            failures.append(f"{route}@{width}: mobile plan hierarchy scroll hint is unclear")
                    if width <= 760 and result["topbarHeight"] and result["topbarHeight"] > 80:
                        failures.append(
                            f"{route}@{width}: mobile topbar is too tall "
                            f"({result['topbarHeight']}px; expected <= 80px)"
                        )
                    if result["renderFailure"]:
                        failures.append(f"{route}@{width}: React page boundary reported render failure")
                    if result["visibleControlsOutsideViewport"]:
                        failures.append(
                            f"{route}@{width}: visible controls outside viewport: "
                            f"{result['visibleControlsOutsideViewport']}"
                        )
                    if page_errors:
                        failures.append(f"{route}@{width}: browser page errors: {page_errors[:3]}")

                    if route in SCREENSHOT_ROUTES:
                        page.screenshot(
                            path=str(ARTIFACTS / f"{route}-{width}.png"),
                            full_page=True,
                            animations="disabled",
                        )

                    if route == "calendar":
                        plan_chip = page.locator(".calendar-plan-chip").filter(has_text="视觉验收计划")
                        try:
                            plan_chip.wait_for(state="visible", timeout=10000)
                        except Exception:
                            failures.append(f"calendar@{width}: deterministic content-plan fixture is missing")
                        else:
                            if plan_chip.count() != 1:
                                failures.append(f"calendar@{width}: expected one content-plan fixture, found {plan_chip.count()}")
                            plan_chip.click()
                            dialog = page.get_by_role("dialog", name="内容计划详情")
                            dialog.wait_for(state="visible", timeout=10000)
                            dialog.locator(".calendar-plan-detail-grid").wait_for(state="visible", timeout=10000)
                            dialog_text = dialog.inner_text()
                            if "未命名草稿" not in dialog_text:
                                failures.append(f"calendar@{width}: missing-title draft fallback is not business-readable")
                            if "internal-draft-id-should-not-show" in dialog_text:
                                failures.append(f"calendar@{width}: internal draft ID leaked into visible plan details")
                            if "987654321" in dialog_text or "internal-task-id-should-not-show" in dialog_text:
                                failures.append(f"calendar@{width}: internal round/task ID leaked into plan details")
                            modal_layout = dialog.evaluate(
                                """element => {
                                  const rect = element.getBoundingClientRect();
                                  return {left: Math.round(rect.left), right: Math.round(rect.right),
                                    width: Math.round(rect.width), scrollWidth: element.scrollWidth,
                                    clientWidth: element.clientWidth};
                                }"""
                            )
                            if modal_layout["left"] < -1 or modal_layout["right"] > width + 1:
                                failures.append(f"calendar@{width}: plan detail modal exceeds viewport: {modal_layout}")
                            if modal_layout["scrollWidth"] > modal_layout["clientWidth"] + 1:
                                failures.append(f"calendar@{width}: plan detail modal has unexpected horizontal overflow: {modal_layout}")
                            page.screenshot(
                                path=str(ARTIFACTS / f"calendar-plan-detail-{width}.png"),
                                full_page=False,
                                animations="disabled",
                            )
                            dialog.get_by_role("button", name="关闭").click()
                            batch_chip = page.locator(".calendar-plan-chip").filter(has_text="生产批次")
                            batch_chip.wait_for(state="visible", timeout=10000)
                            batch_chip.click()
                            batch_dialog = page.get_by_role("dialog", name="生产批次详情")
                            batch_dialog.wait_for(state="visible", timeout=10000)
                            batch_text = batch_dialog.inner_text()
                            internal_ids = (
                                "987654321", "99001", "99002", "99003",
                                "internal-sub-plan-id", "internal-strategy-step-id",
                            )
                            for internal_id in internal_ids:
                                if internal_id in batch_text:
                                    failures.append(f"calendar@{width}: internal batch/version ID leaked: {internal_id}")
                            if batch_text.count("已关联") < 2:
                                failures.append(f"calendar@{width}: batch detail does not show business-readable plan associations")
                            batch_dialog_layout = batch_dialog.evaluate(
                                """element => {
                                  const rect = element.getBoundingClientRect();
                                  return {left: Math.round(rect.left), right: Math.round(rect.right),
                                    scrollWidth: element.scrollWidth, clientWidth: element.clientWidth};
                                }"""
                            )
                            if batch_dialog_layout["left"] < -1 or batch_dialog_layout["right"] > width + 1:
                                failures.append(f"calendar@{width}: batch detail modal exceeds viewport: {batch_dialog_layout}")
                            if batch_dialog_layout["scrollWidth"] > batch_dialog_layout["clientWidth"] + 1:
                                failures.append(f"calendar@{width}: batch detail modal has unexpected horizontal overflow: {batch_dialog_layout}")
                            page.screenshot(
                                path=str(ARTIFACTS / f"calendar-batch-detail-{width}.png"),
                                full_page=False,
                                animations="disabled",
                            )

                # Exercise the actual Operations > Publish confirmation pane.
                # A deterministic pending draft makes the narrow verification inputs visible.
                page_errors.clear()
                page.goto(
                    f"http://127.0.0.1:{port}/?page=operations",
                    wait_until="domcontentloaded",
                )
                page.locator("#main-content").wait_for(state="visible", timeout=10000)
                page.get_by_role("button", name="发布确认").click()
                page.get_by_text("发布确认双通道").wait_for(state="visible", timeout=10000)
                page.locator(".ops-task-verification-actions").wait_for(state="visible", timeout=10000)
                verification_inputs = page.locator(".ops-task-verification-actions .ops-verification-input")
                if verification_inputs.count() < 1:
                    failures.append(f"operations-publish@{width}: verification inputs are missing")
                publish_layout = page.evaluate(
                    """() => ({
                      documentWidth: document.documentElement.scrollWidth,
                      bodyWidth: document.body.scrollWidth,
                      controls: [...document.querySelectorAll('.ops-task-verification-actions input')]
                        .map(element => {
                          const rect = element.getBoundingClientRect();
                          return {left: Math.round(rect.left), right: Math.round(rect.right),
                            width: Math.round(rect.width)};
                        })
                    })"""
                )
                if publish_layout["documentWidth"] > width + 1 or publish_layout["bodyWidth"] > width + 1:
                    failures.append(
                        f"operations-publish@{width}: horizontal overflow "
                        f"(document={publish_layout['documentWidth']}, body={publish_layout['bodyWidth']})"
                    )
                if any(item["left"] < -1 or item["right"] > width + 1 for item in publish_layout["controls"]):
                    failures.append(f"operations-publish@{width}: verification input outside viewport: {publish_layout['controls']}")
                if page_errors:
                    failures.append(f"operations-publish@{width}: browser page errors: {page_errors[:3]}")
                page.screenshot(
                    path=str(ARTIFACTS / f"operations-publish-{width}.png"),
                    full_page=True,
                    animations="disabled",
                )

                page.close()
            browser.close()
    finally:
        _stop_vite(server)

    if failures:
        raise AssertionError(
            "Responsive viewport regression found issues:\n- " + "\n- ".join(failures)
        )
