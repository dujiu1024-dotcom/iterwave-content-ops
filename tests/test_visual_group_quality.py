from __future__ import annotations

from pathlib import Path

from PIL import Image

from src.core.visual_group_quality import validate_visual_group


def _image(path: Path, color: str) -> Path:
    Image.new("RGB", (320, 480), color).save(path, format="PNG")
    return path


def _ocr(count: int) -> dict:
    return {
        "status": "passed",
        "gate": "passed",
        "pages": [{"status": "passed"} for _ in range(count)],
    }


def test_visual_group_accepts_complete_cover_body_ending_set(tmp_path):
    paths = [
        _image(tmp_path / "cover.png", "red"),
        _image(tmp_path / "body.png", "green"),
        _image(tmp_path / "ending.png", "blue"),
    ]
    report = validate_visual_group(
        paths, roles=["cover", "body", "ending"],
        expected_texts=["封面标题", "正文信息", "结尾总结"],
        ocr_report=_ocr(3), require_ending=True,
    )
    assert report["status"] == "passed"
    assert report["gate"] == "passed"
    assert all(report["checks"].values())


def test_visual_group_rejects_missing_ending_and_ocr_page(tmp_path):
    paths = [
        _image(tmp_path / "cover.png", "red"),
        _image(tmp_path / "body.png", "green"),
    ]
    report = validate_visual_group(
        paths, roles=["cover", "body"],
        expected_texts=["封面标题", "正文信息"],
        ocr_report={"status": "passed", "gate": "passed", "pages": [{"status": "passed"}]},
        require_ending=True,
    )
    codes = {item["code"] for item in report["failures"]}
    assert report["status"] == "failed"
    assert "page_roles_invalid" in codes
    assert "ocr_page_count_mismatch" in codes


def test_visual_group_rejects_duplicate_images_and_inconsistent_dimensions(tmp_path):
    first = _image(tmp_path / "first.png", "red")
    duplicate = tmp_path / "duplicate.png"
    duplicate.write_bytes(first.read_bytes())
    third = tmp_path / "third.png"
    Image.new("RGB", (480, 320), "blue").save(third, format="PNG")
    report = validate_visual_group(
        [first, duplicate, third],
        roles=["cover", "body", "ending"],
        expected_texts=["封面标题", "正文信息", "结尾总结"],
        ocr_report=_ocr(3), require_ending=True,
    )
    codes = {item["code"] for item in report["failures"]}
    assert "duplicate_page_image" in codes
    assert "page_aspect_ratio_inconsistent" in codes


def test_visual_group_fails_closed_when_ocr_not_passed(tmp_path):
    paths = [
        _image(tmp_path / "cover.png", "red"),
        _image(tmp_path / "body.png", "green"),
        _image(tmp_path / "ending.png", "blue"),
    ]
    report = validate_visual_group(
        paths, roles=["cover", "body", "ending"],
        expected_texts=["封面标题", "正文信息", "结尾总结"],
        ocr_report={"status": "unavailable", "gate": "not_passed"},
        require_ending=True,
    )
    assert report["status"] == "failed"
    assert "group_ocr_gate_not_passed" in {item["code"] for item in report["failures"]}
