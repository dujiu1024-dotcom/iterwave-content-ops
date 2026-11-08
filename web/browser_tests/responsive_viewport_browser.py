from __future__ import annotations

import json

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


def _install_dashboard_data_fixtures(page):
    """Contract-shaped, isolated records for Dashboard's read-only data state."""
    fixtures = {
        "/api/rounds": {
            "items": [
                {"id": 4101, "goal_id": "goal-north-star", "round_number": 12,
                 "status": "awaiting_publish", "topic_ids": "[]", "content_variants": "[]",
                 "plan_signature": None, "created_at": "2026-11-08T09:30:00+08:00",
                 "awaiting_publish_ended_at": None, "collecting_metrics_started_at": None},
                {"id": 4100, "goal_id": "goal-north-star", "round_number": 11,
                 "status": "producing", "topic_ids": "[]", "content_variants": "[]",
                 "plan_signature": None, "created_at": "2026-11-07T09:30:00+08:00",
                 "awaiting_publish_ended_at": None, "collecting_metrics_started_at": None},
            ],
            "total": 2, "page": 1, "page_size": 20, "pages": 1,
        },
        "/api/goals": [
            {"id": "goal-north-star", "goal_text": "季度品牌认知提升与高质量线索增长目标（长文本布局验证）",
             "target_metric": "有效线索", "target_value": 120, "target_platform": None,
             "metric_scope": "all", "baseline_value": 35, "baseline_at": "2026-10-01T00:00:00+08:00",
             "metric_period_start": "2026-11-01", "metric_period_end": "2026-11-30",
             "data_source": "manual", "current_value": 68, "progress_updated_at": "2026-11-08T08:00:00+08:00",
             "achievement_rate": 56.7, "status": "active", "created_at": "2026-10-01T00:00:00+08:00",
             "goal_level": "north_star", "parent_goal_id": None, "weight": 1, "contribution_note": "测试数据"},
            {"id": "goal-xhs", "goal_text": "小红书平台阶段目标", "target_metric": "互动率",
             "target_value": 8, "target_platform": "xhs", "metric_scope": "platform",
             "baseline_value": 3, "baseline_at": "2026-10-01T00:00:00+08:00",
             "metric_period_start": "2026-11-01", "metric_period_end": "2026-11-30",
             "data_source": "manual", "current_value": 5, "progress_updated_at": "2026-11-08T08:00:00+08:00",
             "achievement_rate": 62.5, "status": "active", "created_at": "2026-10-01T00:00:00+08:00",
             "goal_level": "platform", "parent_goal_id": "goal-north-star", "weight": 1, "contribution_note": "阶段目标"},
        ],
        "/api/goals/summary": {
            "percent": 56.7, "method": "equal_weight_mean", "active_goals": 2,
            "counted_goals": 2, "pending_goals": 0, "period_start": "2026-11-01",
            "period_end": "2026-11-30", "days_left": 22,
            "mode": "hybrid",
            "north_star": {"id": "goal-north-star", "goal_text": "季度品牌认知提升与高质量线索增长目标（长文本布局验证）", "rate": 56.7},
            "by_platform": {"xhs": {"percent": 62.5, "goals": 1, "counted": 1}},
        },
        "/api/config": {
            "editable": {
                "business_timezone": "Asia/Shanghai", "daily_quotas": {},
                "ops_window": {"start_hour": 9, "end_hour": 21}, "cooldown_minutes": 30,
                "hot_topics": {"heat_threshold": 1000, "heat_filter_enabled": True,
                               "time_window_hours": 24, "time_filter_enabled": True},
            },
            "locked": {"dimension_match_required": True, "publishability_required": True},
            "readonly": {
                "persona_configured": True, "persona_keywords": ["品牌运营", "用户体验"],
                "forbidden_directions": ["未经证实的功效承诺"], "redline_keywords_count": 1,
                "kb_path": "", "kb_type": "none", "kb_table": "", "kb_field_mapping": {},
                "llm_provider": "fixture", "llm_model": "fixture", "llm_fallback_provider": "",
                "llm_fallback_model": "", "llm_base_url": "", "llm_fallback_base_url": "",
                "llm_thinking": "", "llm_channel": "plan", "llm_temperature": 0.2,
                "llm_timeout_seconds": 30, "llm_enabled": True, "llm_has_api_key": False,
                "llm_fallback_has_api_key": False, "platforms": {}, "timeouts": {},
                "scoring": {"cold_start": {}, "sample_accumulating": {}, "stable_operation": {},
                            "timeliness_half_life_days": {}, "active_dimension_min_uses": 1,
                            "active_dimension_window_days": 30, "shrinkage_m": 1},
                "rhythm_intervals": {}, "redline_keywords": [], "notification_channels": {},
            },
            "overrides_file": "", "overrides": {}, "audit": [],
        },
        "/api/hot_topics": {"items": [
            {"id": "hot-fixture-1", "title": "平台内容治理新规发布：品牌账号运营需要关注的五个变化",
             "source": "xhs", "discovered_at": "2026-11-08T08:00:00+08:00", "url": None,
             "heat": 128000, "heat_unit": "热度", "time_sensitive": 1, "evidence_status": "collected",
             "dimension": "品牌运营", "dimension_match": True, "dimension_hits": ["品牌运营"],
             "match_score": 91, "match_angle": "行业政策", "match_reason": "测试 fixture",
             "match_method": "keyword", "last_seen_at": "2026-11-08T08:00:00+08:00",
             "collected_at": "2026-11-08T08:00:00+08:00", "trend": "爆发", "trend_reason": "测试",
             "heat_prev": 90000, "heat_peak": 128000, "platform": "xhs", "seen_count": 2,
             "also_on": [], "publishable": True, "publishability_status": "可发布", "sensitive_hits": []},
            {"id": "hot-fixture-2", "title": "待研判热点：行业案例与用户体验趋势",
             "source": "douyin", "discovered_at": "2026-11-08T07:00:00+08:00", "url": None,
             "heat": 42000, "heat_unit": "热度", "time_sensitive": 0, "evidence_status": "collected",
             "dimension": None, "dimension_match": None, "dimension_hits": [], "match_score": None,
             "match_angle": None, "match_reason": None, "match_method": None,
             "last_seen_at": None, "collected_at": None, "trend": "平稳", "trend_reason": "测试",
             "heat_prev": None, "heat_peak": None, "platform": "douyin", "seen_count": 1,
             "also_on": [], "publishable": False, "publishability_status": "未研判", "sensitive_hits": []},
        ], "total": 2, "page": 1, "page_size": 20, "pages": 1},
        "/api/insights": {"items": [
            {"id": 801, "round_id": 4100, "platform": "xhs",
             "analyzed_at": "2026-11-08T07:00:00+08:00", "days_analyzed": 7,
             "trend": {"avg": 5.2, "p25": 3.1, "p50": 4.8, "p75": 6.4},
             "best_hours": [12, 20], "best_title_style": {"style": "问题导向"},
             "best_card_style": "清单型", "top_dimensions": ["品牌运营", "用户体验"],
             "sample_count": 12, "avg_interaction_rate": 5.2,
             "conclusion": "结构化标题与清单型封面在本周样本中表现更稳定，建议继续验证。",
             "summary": "本周样本复盘", "confidence": 0.82,
             "evidence_days": [{"date": "2026-11-07", "avg": 5.2, "drafts": 3}],
             "suggestions": ["继续测试问题型标题"]},
        ]},
        "/api/platform_progress": {
            "xhs": {"enabled": True, "configured": True, "supported": True, "account_registered": True,
                    "account_active": True, "credential_present": True, "session_validity": "not_checked",
                    "preflight_ready": False, "blockers": ["测试环境未连接平台"], "total_drafts": 6,
                    "status_breakdown": {"draft_saved": 2, "user_published": 1, "data_fetching": 1},
                    "circuit_broken": False},
            "douyin": {"enabled": True, "configured": True, "supported": True, "account_registered": True,
                       "account_active": True, "credential_present": False, "session_validity": "not_checked",
                       "preflight_ready": False, "blockers": ["未配置登录态"], "total_drafts": 3,
                       "status_breakdown": {"draft_pending": 2, "draft_saved": 1}, "circuit_broken": False},
            "kuaishou": {"enabled": False, "configured": False, "supported": True, "account_registered": False,
                         "account_active": False, "credential_present": False, "session_validity": "not_checked",
                         "preflight_ready": False, "blockers": [], "total_drafts": 0,
                         "status_breakdown": {}, "circuit_broken": False},
            "wechat_channels": {"enabled": True, "configured": True, "supported": True, "account_registered": True,
                                "account_active": False, "credential_present": True, "session_validity": "not_checked",
                                "preflight_ready": False, "blockers": ["账号暂不可用"], "total_drafts": 1,
                                "status_breakdown": {"failed": 1}, "circuit_broken": True},
        },
    }

    def handle(route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        for endpoint, payload in fixtures.items():
            if path.endswith(endpoint):
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))
        if path.endswith("/api/todos"):
            return route.fulfill(status=200, content_type="application/json",
                body=json.dumps({"items": [{"id": "todo-fixture-1", "record_id": 7401, "source": "global_queue", "tenant_id": "default", "tenant_name": "Viewport test", "status": "pending",
                    "title": "请确认本周品牌运营策略调整建议",
                    "detail": "测试长备注：基于近期内容表现、平台差异和样本不足情况，需要人工确认是否调整下一轮生产方向。",
                    "severity": "high", "source_label": "策略建议", "created_at": "2026-11-08T08:00:00+08:00",
                    "target_page": "goals", "target_tab": None}], "total": 1, "counts": {"global_queue": 1}, "page": 1, "page_size": 100, "pages": 1, "capped": False}, ensure_ascii=False))
        return route.fallback()

    page.route("**/api/**", handle)


def test_dashboard_structured_data_state_at_required_viewports():
    server, port = _serve_vite()
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
                _install_dashboard_data_fixtures(page)
                page.goto(f"http://127.0.0.1:{port}/?page=dashboard", wait_until="domcontentloaded")
                page.locator("#main-content").wait_for(state="visible", timeout=10000)
                page.get_by_text("请确认本周品牌运营策略调整建议", exact=True).wait_for(state="visible", timeout=15000)
                page.get_by_text("平台内容治理新规发布：品牌账号运营需要关注的五个变化", exact=True).wait_for(state="visible", timeout=10000)
                assert page.locator(".dashboard-partial-warning").count() == 0, "Dashboard fixture should resolve all eight data sources"
                page.screenshot(path=str(ARTIFACTS / f"dashboard-data-state-{width}.png"),
                                full_page=True, animations="disabled")
                metrics = page.evaluate("""() => ({
                    documentWidth: document.documentElement.scrollWidth,
                    bodyWidth: document.body.scrollWidth,
                    mainWidth: document.querySelector('#main-content')?.clientWidth ?? 0,
                    visibleRows: document.querySelectorAll('.task-row').length
                })""")
                if metrics["documentWidth"] > width + 1 or metrics["bodyWidth"] > width + 1:
                    raise AssertionError(f"dashboard@{width}: horizontal overflow: {metrics}")
                if metrics["visibleRows"] < 2:
                    raise AssertionError(f"dashboard@{width}: expected structured business rows: {metrics}")
                if page_errors:
                    raise AssertionError(f"dashboard@{width}: browser errors: {page_errors}")
                page.close()
            browser.close()
    finally:
        _stop_vite(server)

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