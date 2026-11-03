import pytest

from src.core import ai_card_generation as cards


class _Cfg:
    class media_generation:
        max_dimension = 20000
        max_pixels = 40_000_000


def test_ai_card_generation_calls_image_api_for_cover_and_every_body_page(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        calls.append(prompt) or (b"image", "image/png", ".png", 1080, 1440)
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report: {"status": "passed", "gate": "passed"},
    )
    paths, pages = cards.generate_ai_card_set(
        title="测试标题", subtitle="运营", body="第一条内容。第二条内容。",
        output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
        cfg=_Cfg(), settings={"model": "image-model", "size": "1024x1536"},
    )
    assert len(paths) == len(calls) == len(pages) + 1
    assert "完整、可发布" in calls[0]
    assert "不是背景图" in calls[0]
    assert "必须逐字、完整呈现" in calls[1]
    assert all(path.exists() for path in paths)


def test_ai_card_generation_fails_closed_when_ocr_does_not_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        b"image", "image/png", ".png", 1080, 1440
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report: {"status": "unavailable", "gate": "not_passed"},
    )
    with pytest.raises(ValueError, match="连续 3 次未通过 OCR"):
        cards.generate_ai_card_set(
            title="标题", subtitle="栏目", body="正文内容。",
            output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
            cfg=_Cfg(), settings={"model": "image-model", "size": "1024x1536"},
        )


def test_ai_card_generation_keeps_images_when_ocr_failure_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        b"image", "image/png", ".png", 1080, 1440
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report: {"status": "unavailable", "gate": "not_passed"},
    )
    paths, pages = cards.generate_ai_card_set(
        title="标题", subtitle="栏目", body="正文内容。",
        output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
        cfg=_Cfg(), settings={"model": "image-model", "size": "1024x1536"},
        allow_ocr_failure=True,
    )
    assert len(paths) == len(pages) + 1
    assert all(path.exists() for path in paths)


def test_pagination_does_not_drop_long_sentence_content():
    source = "这是一个很长的句子，" * 40 + "最后一句。"
    pages = cards._paginate_body(source, max_chars=40, segments_per_page=3)
    joined = "".join(segment for page in pages for segment in page)
    assert joined == source
