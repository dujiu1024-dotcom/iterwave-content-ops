import sqlite3

import pytest
from pathlib import Path
from types import SimpleNamespace

from src.core import ai_card_generation as cards


class _Cfg:
    class media_generation:
        max_dimension = 20000
        max_pixels = 40_000_000

    def __init__(self, tmp_path):
        db_path = tmp_path / "visual.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE visual_generation_tasks (
                task_id TEXT PRIMARY KEY, account_group TEXT NOT NULL, status TEXT NOT NULL,
                owner TEXT, lease_expires_at REAL, started_at REAL, heartbeat_at REAL,
                finished_at REAL, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE visual_generation_pages (
                task_id TEXT NOT NULL, page_index INTEGER NOT NULL, status TEXT NOT NULL,
                output_path TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
                updated_at REAL NOT NULL, PRIMARY KEY(task_id, page_index)
            );
            CREATE TABLE visual_generation_task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                from_status TEXT, to_status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL
            );
            CREATE TABLE visual_generation_account_leases (
                account_group TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                owner TEXT NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL
            );
        """)
        conn.close()
        self.paths = type("Paths", (), {"db_file": str(db_path)})()
        self.platforms = type("Platforms", (), {
            "xhs": type("Account", (), {"account_key": "test-account"})()
        })()


def test_ai_card_generation_calls_image_api_for_cover_and_every_body_page(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        calls.append(prompt) or (b"image", "image/png", ".png", 1024, 1536)
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report, **kwargs: {"status": "passed", "gate": "passed"},
    )
    paths, pages = cards.generate_ai_card_set(
        title="测试标题", subtitle="运营", body="第一条内容。第二条内容。",
        output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
        cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
    )
    assert len(paths) == len(calls) == len(pages) + 1
    assert "完整、可发布" in calls[0]
    assert "不是背景图" in calls[0]
    assert "必须逐字、完整呈现" in calls[1]
    assert all(path.exists() for path in paths)


def test_ai_card_generation_fails_closed_when_ocr_does_not_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        b"image", "image/png", ".png", 1024, 1536
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report, **kwargs: {"status": "failed", "gate": "not_passed"},
    )
    with pytest.raises(ValueError, match="连续 3 次未通过 OCR"):
        cards.generate_ai_card_set(
            title="标题", subtitle="栏目", body="正文内容。",
            output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
            cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
        )


def test_pagination_does_not_drop_long_sentence_content():
    source = "这是一个很长的句子，" * 40 + "最后一句。"
    pages = cards._paginate_body(source, max_chars=40, segments_per_page=3, max_pages=50)
    joined = "".join(segment for page in pages for segment in page)
    assert joined == source


def test_ai_card_generation_reuses_compatible_validated_pages(monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image

    image_buffer = BytesIO()
    Image.new("RGB", (1024, 1536), "white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    calls = []
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        calls.append(prompt) or (image_bytes, "image/png", ".png", 1024, 1536)
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report, **kwargs: {"status": "passed", "gate": "passed"},
    )
    kwargs = dict(
        title="测试标题", subtitle="运营", body="第一条内容。第二条内容。",
        output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
        cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
    )
    first_paths, _ = cards.generate_ai_card_set(**kwargs)
    first_call_count = len(calls)
    second_paths, _ = cards.generate_ai_card_set(**kwargs)
    assert first_call_count == 2
    assert len(calls) == first_call_count
    assert len(first_paths) == len(second_paths) == 2
    assert all(path.exists() for path in second_paths)


def test_ai_card_generation_cleans_staging_after_group_failure(monkeypatch, tmp_path):
    staging_root = tmp_path / "media" / "staging"
    other_task = staging_root / "other-task"
    other_task.mkdir(parents=True)
    sentinel = other_task / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    delivered_dir = tmp_path / "media" / "sets" / "previous-task"
    delivered_dir.mkdir(parents=True)
    previous_delivery = delivered_dir / "already-delivered.png"
    previous_delivery.write_bytes(b"keep-delivered")
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        b"not-a-real-image", "image/png", ".png", 1024, 1536
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report, **kwargs: {"status": "failed", "gate": "not_passed"},
    )
    with pytest.raises(ValueError, match="连续 3 次未通过 OCR"):
        cards.generate_ai_card_set(
            title="标题", subtitle="栏目", body="正文内容。",
            output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
            cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
        )
    assert staging_root.exists()
    assert list(staging_root.iterdir()) == [other_task]
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert previous_delivery.read_bytes() == b"keep-delivered"
    assert not list(tmp_path.glob("ai_card_*"))


def test_ai_card_generation_regenerates_corrupt_cached_page(monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image

    image_buffer = BytesIO()
    Image.new("RGB", (1024, 1536), "white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    calls = []
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        calls.append(prompt) or (image_bytes, "image/png", ".png", 1024, 1536)
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report, **kwargs: {"status": "passed", "gate": "passed"},
    )
    kwargs = dict(
        title="测试标题", subtitle="运营", body="第一条内容。",
        output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
        cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
    )
    cards.generate_ai_card_set(**kwargs)
    cached_image = next((tmp_path / "media" / "validated").glob("*/image.png"))
    cached_image.write_bytes(b"corrupt-cache")
    cards.generate_ai_card_set(**kwargs)
    assert len(calls) == 3


def test_ai_card_generation_reuses_completed_pages_on_retry(monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image

    image_buffer = BytesIO()
    Image.new("RGB", (1024, 1536), "white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    calls = []
    monkeypatch.setattr(cards, "generate_image_bytes", lambda prompt, **kwargs: (
        calls.append(prompt) or (image_bytes, "image/png", ".png", 1024, 1536)
    ))
    monkeypatch.setattr(
        "src.core.visual_ocr.validate_rendered_pages",
        lambda images, expected, report, **kwargs: {"status": "passed", "gate": "passed"},
    )
    kwargs = dict(
        title="稳定任务", subtitle="运营", body="第一页内容。第二页内容。",
        output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
        cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
        task_id="stable-task-id", account_group="test-account",
    )
    first, _ = cards.generate_ai_card_set(**kwargs)
    call_count = len(calls)
    second, _ = cards.generate_ai_card_set(**kwargs)
    assert len(calls) == call_count
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_ocr_preflight_failure_stops_before_any_paid_image_call(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "src.core.visual_ocr.preflight_ocr",
        lambda **_kwargs: {"status": "unavailable", "reason": "ocr_language_missing"},
    )
    monkeypatch.setattr(
        cards, "generate_image_bytes",
        lambda *args, **kwargs: calls.append(args) or (b"image", "image/png", ".png", 1, 1),
    )
    with pytest.raises(RuntimeError, match="OCR 前置预检失败"):
        cards.generate_ai_card_set(
            title="标题", subtitle="栏目", body="正文内容。",
            output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
            cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
        )
    assert calls == []


def test_pagination_rejects_more_than_nine_total_pages():
    source = "内容。" * 1000
    with pytest.raises(ValueError, match="超过单篇最大"):
        cards._paginate_body(source, max_chars=10, segments_per_page=1, max_pages=9)



def test_ocr_becoming_unavailable_stops_without_paid_retry(monkeypatch, tmp_path):
    from src.core import visual_ocr

    calls = []
    monkeypatch.setattr(visual_ocr, "preflight_ocr", lambda **_kwargs: {"status": "available"})
    monkeypatch.setattr(
        cards, "generate_image_bytes",
        lambda *args, **kwargs: calls.append(args) or (b"image", "image/png", ".png", 1024, 1536),
    )
    monkeypatch.setattr(
        visual_ocr, "validate_rendered_pages",
        lambda *args, **kwargs: {"status": "unavailable", "reason": "ocr_execution_failed"},
    )
    with pytest.raises(RuntimeError, match="立即终止，不再进行付费重试"):
        cards.generate_ai_card_set(
            title="标题", subtitle="栏目", body="正文内容。",
            output_dir=tmp_path, platform=type("P", (), {"value": "xhs"})(),
            cfg=_Cfg(tmp_path), settings={"model": "image-model", "size": "1024x1536"},
        )
    assert len(calls) == 1
