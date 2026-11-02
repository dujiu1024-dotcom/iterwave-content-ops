from pathlib import Path

import pytest

from src.core.visual_renderer import _document, _pages, _split_paragraphs, _validate_theme_contrast
from src.core.visual_specs import build_infographic_components


def test_long_paragraph_is_split_without_losing_text():
    source = "中文内容，" * 100
    chunks = _split_paragraphs(source, limit=40)
    assert "".join(chunks) == source
    assert all(len(chunk) <= 40 for chunk in chunks)


def test_page_limit_fails_instead_of_silently_truncating():
    with pytest.raises(ValueError, match="不允许静默截断"):
        _pages("\n".join(f"段落{i}" for i in range(40)), max_pages=2, capacity=2)


def test_page_planner_preserves_all_paragraphs():
    source = "\n".join(f"段落{i}" for i in range(11))
    pages = _pages(source, max_pages=5, capacity=3)
    assert [item for page in pages for item in page] == [f"段落{i}" for i in range(11)]


def test_html_escapes_user_content_and_renders_subtitle_separately():
    document = _document(
        title="<script>alert(1)</script>",
        subtitle="真实副标题",
        paragraphs=["正文内容"],
        footer="",
        page_text="2/2",
        theme="editorial_magazine",
        cover=True,
        width=1080,
        height=1440,
    )
    assert "<script>" not in document
    assert "&lt;script&gt;" in document
    assert "真实副标题" in document
    assert "正文内容" not in document


def test_unknown_theme_uses_safe_default():
    document = _document(
        title="标题", subtitle="", paragraphs=["正文"], footer="", page_text="1/2",
        theme="untrusted-theme", cover=False, width=1080, height=1440,
    )
    assert "#F3F5F7" in document



def test_document_embeds_optional_background_data_uri():
    document = _document(
        title="标题", subtitle="副标题", paragraphs=["正文"], footer="", page_text="1/2",
        theme="knowledge_infographic", cover=False, width=1080, height=1440,
        background_data_uri="data:image/png;base64,ZmFrZQ==",
    )
    assert "data:image/png;base64,ZmFrZQ==" in document
    assert "background-size:cover" in document


def test_all_builtin_themes_pass_contrast_hard_gate():
    for theme in ("knowledge_infographic", "editorial_magazine", "business_poster", "lifestyle"):
        assert _validate_theme_contrast(theme, False) >= 4.5
        assert _validate_theme_contrast(theme, True) >= 4.5


def test_page_planner_respects_text_budget_even_when_paragraph_count_fits():
    source = "\n".join("内容" * 100 for _ in range(8))
    chunks = _split_paragraphs(source)
    pages = _pages(source, max_pages=6, capacity=5, max_page_chars=320)
    assert [block for page in pages for block in page] == chunks
    assert all(sum(len(block) for block in page) <= 320 for page in pages)


def test_infographic_component_schema_preserves_copy_and_classifies_visual_blocks():
    source = ["第一步：收集样本", "覆盖率提升25%，达到100人", "“教师应保留最终判断权”", "持续复盘内容"]
    components = build_infographic_components(source)
    assert [item.kind for item in components] == ["step", "stat", "quote", "text"]
    assert [item.text for item in components] == source


def test_document_renders_semantic_infographic_components():
    document = _document(
        title="数据复盘", subtitle="知识卡片",
        paragraphs=["第一步：收集样本", "覆盖率提升25%，达到100人", "“教师应保留最终判断权”"],
        footer="", page_text="2/2", theme="knowledge_infographic",
        cover=False, width=1080, height=1440,
    )
    assert "visual-step" in document
    assert "visual-stat" in document
    assert "visual-quote" in document
    assert "<strong>25%</strong>" in document


def test_document_supports_tenant_custom_design_tokens_and_component_styles():
    document = _document(
        title="自定义品牌", subtitle="", paragraphs=["正文"], footer="", page_text="2/2",
        theme="knowledge_infographic", cover=False, width=1080, height=1440,
        palette_override={
            "bg": "#FAFAFA", "fg": "#121212", "accent": "#006699",
            "muted": "#555555", "component_style": "minimal",
        },
    )
    assert "background:#FAFAFA" in document
    assert "color:#121212" in document
    assert "padding:14px 0;border:0;border-radius:0;background:transparent" in document


def test_document_rejects_unknown_component_style():
    with pytest.raises(ValueError, match="组件样式"):
        _document(
            title="标题", subtitle="", paragraphs=["正文"], footer="", page_text="2/2",
            theme="knowledge_infographic", cover=False, width=1080, height=1440,
            palette_override={"component_style": "untrusted-css"},
        )
