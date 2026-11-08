from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path
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