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
SCREENSHOT_ROUTES = {
    "dashboard", "global-ops", "plan-center", "production", "review",
    "settings", "task-queue",
}


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
                            return {
                              tag: element.tagName.toLowerCase(),
                              label: element.getAttribute('aria-label')
                                || element.textContent?.trim().slice(0, 48)
                                || element.getAttribute('placeholder') || '',
                              left: Math.round(rect.left),
                              right: Math.round(rect.right),
                              width: Math.round(rect.width),
                            };
                          }).filter((item) => item.left < -1 || item.right > innerWidth + 1);
                          const main = document.querySelector('#main-content');
                          const topbar = document.querySelector('.topbar');
                          return {
                            viewport: innerWidth,
                            documentWidth: document.documentElement.scrollWidth,
                            bodyWidth: document.body.scrollWidth,
                            mainRight: main ? Math.round(main.getBoundingClientRect().right) : null,
                            topbarRight: topbar ? Math.round(topbar.getBoundingClientRect().right) : null,
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

                page.close()
            browser.close()
    finally:
        _stop_vite(server)

    if failures:
        raise AssertionError(
            "Responsive viewport regression found issues:\n- " + "\n- ".join(failures)
        )
