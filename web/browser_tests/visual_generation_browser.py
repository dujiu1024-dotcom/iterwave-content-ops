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
