from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from src.core.visual_asset_selection import select_visual_assets


def _asset(asset_id: int, filename: str, raw: bytes, **overrides):
    return {
        "asset_id": asset_id,
        "version_no": 1,
        "title": "产品使用场景",
        "description": "真实产品图片",
        "stored_filename": filename,
        "mime_type": "image/png",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "review_status": "confirmed",
        "commercial_use_status": "allowed",
        "privacy_status": "clear",
        "readability_status": "readable",
        "completeness_status": "complete",
        "duplicate_status": "unique",
        "status": "active",
        **overrides,
    }


def _png(path: Path, color=(20, 40, 60)) -> bytes:
    image = Image.new("RGB", (600, 900), color)
    image.save(path, format="PNG")
    return path.read_bytes()


def test_selects_verified_image_and_returns_traceable_evidence(tmp_path):
    raw = _png(tmp_path / "one.png")
    result = select_visual_assets(
        candidates=[_asset(7, "one.png", raw)],
        media_dir=tmp_path,
        topic="产品使用场景",
        body="产品展示与使用方法",
        usage_role="cover",
    )
    assert [item["asset_id"] for item in result["selected"]] == [7]
    selected = result["selected"][0]
    assert selected["verified_path"] == tmp_path / "one.png"
    assert selected["sha256"] == hashlib.sha256(raw).hexdigest()
    assert selected["width"] == 600
    assert selected["height"] == 900


def test_rejects_unapproved_privacy_quality_and_archived_assets(tmp_path):
    raw = _png(tmp_path / "one.png")
    candidates = [
        _asset(1, "one.png", raw, privacy_status="unknown"),
        _asset(2, "one.png", raw, commercial_use_status="restricted"),
        _asset(3, "one.png", raw, duplicate_status="possible_duplicate"),
        _asset(4, "one.png", raw, status="archived"),
    ]
    result = select_visual_assets(
        candidates=candidates, media_dir=tmp_path, topic="产品", body="产品",
    )
    assert not result["selected"]
    assert {item["asset_id"] for item in result["rejected"]} == {1, 2, 3, 4}


def test_rejects_content_duplicate_even_when_asset_ids_differ(tmp_path):
    raw = _png(tmp_path / "one.png")
    (tmp_path / "two.png").write_bytes(raw)
    result = select_visual_assets(
        candidates=[
            _asset(1, "one.png", raw),
            _asset(2, "two.png", raw),
        ],
        media_dir=tmp_path,
        topic="产品",
        body="产品图片",
    )
    assert len(result["selected"]) == 1
    assert result["rejected"] == [
        {"asset_id": 2, "reason": "duplicate_image_content"}
    ]


def test_rejects_path_traversal_corrupt_file_and_hash_mismatch(tmp_path):
    raw = _png(tmp_path / "one.png")
    (tmp_path / "broken.png").write_bytes(b"not an image")
    candidates = [
        _asset(1, "../one.png", raw),
        _asset(2, "broken.png", b"not an image"),
        _asset(3, "one.png", raw, sha256="0" * 64),
    ]
    result = select_visual_assets(
        candidates=candidates, media_dir=tmp_path, topic="产品", body="产品",
    )
    assert not result["selected"]
    assert {item["reason"] for item in result["rejected"]} == {
        "unsafe_stored_filename", "image_decode_failed",
        "source_version_hash_mismatch",
    }


def test_rejects_missing_or_unrecognized_classifications(tmp_path):
    raw = _png(tmp_path / "one.png")
    candidate = _asset(9, "one.png", raw, review_status="pending")
    result = select_visual_assets(
        candidates=[candidate], media_dir=tmp_path, topic="产品", body="产品",
    )
    assert result["selected"] == []
    assert result["rejected"][0]["reason"] == "review_status:pending"
