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
ARTIFACTS = ROOT / "test-results" / "structured-diagnostics"


def _start_vite():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=WEB, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
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


def test_structured_diagnostics_disclosure_and_sensitive_filtering():
    server, port = _start_vite()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width, height in ((1440, 900), (390, 844)):
                page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(
                    f"http://127.0.0.1:{port}/browser_tests/fixtures/structured-data-diagnostics.html",
                    wait_until="networkidle",
                )
                details = page.locator("details.structured-data-diagnostics")
                details.wait_for(state="attached", timeout=10000)
                assert details.evaluate("(element) => !element.open"), "diagnostics must start collapsed"
                assert not details.locator("code").first.is_visible(), "diagnostic values must not be visible before expansion"
                page.screenshot(path=str(ARTIFACTS / f"diagnostics-collapsed-{width}.png"), full_page=True)

                details.locator("summary").click()
                assert details.evaluate("(element) => element.open"), "summary click must expand diagnostics"
                for expected in ("trace-visible-2026-qa", "worker-visible-42", "request-visible-31",
                                 "visible-safe-detail", "serialized-safe-detail"):
                    assert details.get_by_text(expected, exact=False).count() > 0, f"missing safe diagnostic: {expected}"
                diagnostic_text = details.inner_text()
                for secret in ("NEVER_RENDER_PASSWORD_91", "NEVER_RENDER_API_KEY_72",
                               "NEVER_RENDER_TOKEN_53", "NEVER_RENDER_PRIVATE_KEY_18"):
                    assert secret not in diagnostic_text, f"sensitive value leaked: {secret}"

                code = details.locator("code").first
                assert code.is_visible()
                assert code.evaluate("(element) => getComputedStyle(element).overflowWrap") == "anywhere"
                layout = page.evaluate("""() => ({
                  viewport: innerWidth,
                  documentWidth: document.documentElement.scrollWidth,
                  bodyWidth: document.body.scrollWidth,
                  codeRight: Math.round(document.querySelector('details code').getBoundingClientRect().right),
                  rootRight: Math.round(document.querySelector('#root').getBoundingClientRect().right)
                })""")
                assert layout["documentWidth"] <= width + 1, f"horizontal overflow at {width}px: {layout}"
                assert layout["bodyWidth"] <= width + 1, f"body overflow at {width}px: {layout}"
                assert layout["codeRight"] <= width + 1, f"diagnostic code exceeds viewport at {width}px: {layout}"
                page.screenshot(path=str(ARTIFACTS / f"diagnostics-expanded-{width}.png"), full_page=True)
                assert not errors, f"browser errors at {width}px: {errors}"
                page.close()
            browser.close()
    finally:
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=10)
