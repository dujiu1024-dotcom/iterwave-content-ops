from pathlib import Path

from src.core.visual_renderer import _document


def test_layout_document_has_semantic_regions_for_geometry_gate():
    document = _document(
        title="标题", subtitle="副标题", paragraphs=["正文一", "正文二"],
        footer="品牌落款", page_text="2/2", theme="knowledge_infographic",
        cover=False, width=1080, height=1440,
    )
    assert 'class="canvas"' in document
    assert 'class="body"' in document
    assert 'class="footer"' in document
    assert 'class="page"' in document


def test_long_unbroken_text_is_wrapped_in_css():
    document = _document(
        title="A" * 300, subtitle="", paragraphs=["B" * 300], footer="",
        page_text="1/2", theme="knowledge_infographic", cover=True,
        width=1080, height=1440,
    )
    assert "overflow-wrap:anywhere" in document
    assert "word-break:break-word" in document
