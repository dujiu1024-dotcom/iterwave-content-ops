from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = os.path.join(ROOT, "web")


def _serve_vite():
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
            raise RuntimeError("Vite exited before readiness")
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=1):
                return process, port
        except Exception:
            time.sleep(0.25)
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=10)
    raise TimeoutError("Vite did not become ready")


def _stop_vite(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def test_compact_filter_and_form_layouts_at_four_viewports():
    server, port = _serve_vite()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(f"http://127.0.0.1:{port}/")
            page.set_content("""
              <main style="width:100%;max-width:1200px;margin:auto">
                <section id="review" class="review-filters">
                  <label>状态<select><option>全部</option></select></label>
                  <label>平台<select><option>全部</option></select></label>
                  <label>类型<select><option>全部</option></select></label>
                  <label>关键词<input type="search"></label>
                  <label>开始日期<input type="date"></label>
                  <label>结束日期<input type="date"></label>
                </section>
                <section id="calendar" class="head-actions calendar-toolbar filter-bar">
                  <button class="btn sm">今天</button><input class="field calendar-date-picker" type="date">
                  <input class="field calendar-search" type="search"><select class="field"><option>全部状态</option></select>
                  <select class="field"><option>全部平台</option></select><button class="btn sm">刷新</button>
                </section>
                <section id="topic" class="toolbar topic-pool-toolbar">
                  <div class="tabs"><button>可用</button></div>
                  <form class="flex filter-row"><input class="field" style="width:180px"><button class="btn sm">搜索</button></form>
                  <select class="field"><option>全部方向</option></select><select class="field"><option>全部来源</option></select>
                </section>
                <section id="goal" class="grid four goal-form-grid">
                  <div><textarea class="field"></textarea></div>
                  <div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div><div><input class="field"></div>
                </section>
              </main>
            """)
            page.add_style_tag(url=f"http://127.0.0.1:{port}/src/styles.css")
            for width, expected_review_cols in [(1440, 6), (1024, 6), (768, 3), (390, 2)]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(80)
                metrics = page.evaluate("""() => {
                  const rect = (el) => { const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
                  const review = document.querySelector('#review');
                  const labels = [...review.querySelectorAll('label')].map(rect);
                  const reviewCols = new Set(labels.map(x => Math.round(x.x)));
                  const allInputs = [...document.querySelectorAll('input,select,textarea')];
                  const overflow = document.documentElement.scrollWidth > window.innerWidth;
                  const goal = document.querySelector('#goal');
                  const goalChildren = [...goal.children].map(rect);
                  return {
                    reviewCols: reviewCols.size,
                    reviewLabels: labels,
                    controlWidths: allInputs.map(x => rect(x).width),
                    overflow,
                    goalFirst: goalChildren[0],
                    viewport: window.innerWidth
                  };
                }""")
                assert not metrics["overflow"], f"horizontal overflow at {width}px: {metrics}"
                assert metrics["reviewCols"] == expected_review_cols, f"review grid columns wrong at {width}px: {metrics}"
                assert all(w <= width for w in metrics["controlWidths"]), f"control wider than viewport at {width}px"
                assert metrics["goalFirst"]["width"] <= width, f"goal description field overflow at {width}px"
            browser.close()
    finally:
        _stop_vite(server)
