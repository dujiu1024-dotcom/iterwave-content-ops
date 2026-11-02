from pathlib import Path

import pytest

from src.adapters.base import Platform
from src.core.visual_renderer import render_card_set


def test_chromium_renderer_produces_cover_and_body_pages(tmp_path: Path, monkeypatch):
    # Chromium output/layout is tested here; OCR policy is tested independently in
    # test_visual_ocr.py so this integration test does not depend on runner OCR accuracy.
    from src.core import visual_ocr
    monkeypatch.setattr(
        visual_ocr, "validate_rendered_pages",
        lambda images, expected, report, **_kw: {"status": "passed", "pages": len(images)},
    )
    paths = render_card_set(
        title="内容生产升级",
        subtitle="真实副标题",
        body="第一段内容\n第二段内容",
        output_dir=tmp_path,
        platform=Platform.XHS,
        max_pages=4,
    )
    assert len(paths) == 2
    assert all(path.is_file() and path.stat().st_size > 1000 for path in paths)


def test_measured_pagination_preserves_text_and_respects_footer(tmp_path: Path, monkeypatch):
    """Long copy is paginated by measured height: nothing lost, no page overflows."""
    from src.core import visual_ocr
    from src.core.visual_renderer import plan_card_pages

    body = "\n".join("这是一段用于测试实测分页的长段落，包含足够多的中文字符来撑满页面。" * 3 for _ in range(10))
    pages = plan_card_pages("长文分页", "测试", body, platform=Platform.XHS, max_pages=9)
    assert len(pages) >= 2
    assert "".join(i for p in pages for i in p).replace(" ", "") == body.replace("\n", "").replace(" ", "")
    monkeypatch.setattr(
        visual_ocr, "validate_rendered_pages",
        lambda images, expected, report, **_kw: {"status": "passed"},
    )
    paths = render_card_set("长文分页", "测试", body, tmp_path, platform=Platform.XHS, pages=pages)
    assert len(paths) == len(pages) + 1


def test_page_limit_raises_instead_of_truncating():
    from src.core.visual_renderer import plan_card_pages

    body = "\n".join("超长内容段落，用来触发页数上限，不允许静默截断。" * 4 for _ in range(30))
    with pytest.raises(ValueError, match="不允许静默截断"):
        plan_card_pages("超长", "测试", body, platform=Platform.XHS, max_pages=3)


def test_renderer_blocks_external_network_requests(tmp_path: Path, monkeypatch):
    from src.core import visual_renderer

    original = visual_renderer._document

    def leaky(**kwargs):
        doc = original(**kwargs)
        return doc.replace("</body>", '<img src="http://example.com/x.png"></body>')

    monkeypatch.setattr(visual_renderer, "_document", leaky)
    with pytest.raises(ValueError, match="外部网络"):
        render_card_set("标题", "副标题", "正文", tmp_path, platform=Platform.XHS)


def test_region_ocr_gate_passes_real_render_and_rejects_wrong_numbers(tmp_path: Path):
    """End-to-end hard gate with real tesseract: correct copy passes, a changed number fails."""
    import shutil

    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    from src.core import visual_ocr, visual_renderer
    from playwright.sync_api import sync_playwright

    paths = render_card_set(
        "10 月数据复盘：3 个指标", "数据复盘",
        "1. 曝光量：128,450 次，环比 +23.6%\n2. 点击率：8.7%，高于均值 6.2%\n3. 涨粉：1,206 人",
        tmp_path, platform=Platform.XHS,
    )
    report = visual_ocr.load_ocr_report(paths)
    assert report["status"] == "passed"
    doc = visual_renderer._document(
        title="10 月数据复盘：3 个指标", subtitle="", paragraphs=["1. 曝光量：128,450 次，环比 +23.6%"],
        footer="", page_text="2/2", theme="knowledge_infographic", cover=False, width=1080, height=1440,
    )
    with sync_playwright() as p:
        iso = visual_renderer._IsolatedPage(p, 1080, 1440)
        layout = iso.load(doc)
        iso.page.screenshot(path=str(tmp_path / "x.png"))
        iso.close()
    with pytest.raises(ValueError, match="OCR"):
        visual_ocr.validate_rendered_pages(
            [tmp_path / "x.png"], ["10 月数据复盘：3 个指标\n1. 曝光量：128,451 次，环比 +23.6%"],
            tmp_path / "r.json", regions=[layout["regions"]], numbers=[layout["numbers"]],
        )
