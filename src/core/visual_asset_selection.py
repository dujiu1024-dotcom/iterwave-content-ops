"""Safe, deterministic selection of task-authorized visual source assets.

This module deliberately accepts the immutable task authorization snapshot and a
tenant-scoped media directory. It does not discover assets globally or mutate usage
records: selection is not evidence that a provider consumed an image.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


_ALLOWED_MIME_FORMATS = {
    "image/jpeg": {"JPEG"},
    "image/png": {"PNG"},
    "image/webp": {"WEBP"},
    "image/gif": {"GIF"},
}
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class VisualAssetSelectionError(ValueError):
    """Raised when the selection request itself is unsafe or invalid."""


def _tokens(value: object) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN.findall(str(value or ""))
        if len(token.strip()) > 1
    }


def _hard_gate_reason(asset: dict[str, Any]) -> str | None:
    """Require affirmative approval for every safety-relevant classification."""
    required = {
        "review_status": {"confirmed"},
        "commercial_use_status": {"allowed"},
        "privacy_status": {"clear", "not_applicable"},
        "readability_status": {"readable", "not_applicable"},
        "completeness_status": {"complete"},
        "duplicate_status": {"unique"},
    }
    for field, allowed in required.items():
        value = str(asset.get(field) or "unknown").strip().lower()
        if value not in allowed:
            return f"{field}:{value}"
    if asset.get("archived_at") or str(asset.get("status") or "active") != "active":
        return "asset_inactive_or_archived"
    return None


def _read_verified_image(
    asset: dict[str, Any], media_dir: Path, *, max_image_bytes: int, max_pixels: int,
) -> tuple[Path, bytes, str, int, int]:
    stored = str(asset.get("stored_filename") or "").strip()
    name = Path(stored)
    if not stored or name.name != stored or stored in {".", ".."}:
        raise VisualAssetSelectionError("unsafe_stored_filename")
    if name.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise VisualAssetSelectionError("unsupported_file_extension")

    root = media_dir.resolve()
    path = (root / stored).resolve()
    if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
        raise VisualAssetSelectionError("file_missing_or_outside_media_root")
    size = path.stat().st_size
    if size <= 0 or size > max_image_bytes:
        raise VisualAssetSelectionError("file_size_out_of_range")

    raw = path.read_bytes()
    if len(raw) != size:
        raise VisualAssetSelectionError("file_changed_during_read")
    mime = str(asset.get("mime_type") or "").lower()
    allowed_formats = _ALLOWED_MIME_FORMATS.get(mime)
    if not allowed_formats:
        raise VisualAssetSelectionError("unsupported_mime_type")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise VisualAssetSelectionError("image_decode_failed") from None
    if image_format not in allowed_formats:
        raise VisualAssetSelectionError("mime_content_mismatch")
    if width < 1 or height < 1 or width * height > max_pixels:
        raise VisualAssetSelectionError("image_dimensions_out_of_range")

    expected_hash = str(asset.get("sha256") or asset.get("source_version") or "").lower()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if _HEX_SHA256.fullmatch(expected_hash) and expected_hash != actual_hash:
        raise VisualAssetSelectionError("source_version_hash_mismatch")
    return path, raw, actual_hash, width, height


def _score_asset(
    asset: dict[str, Any], *, topic: str, body: str, usage_role: str,
    width: int, height: int,
) -> tuple[float, list[str]]:
    target = _tokens(topic) | _tokens(body)
    metadata = " ".join(
        str(asset.get(key) or "")
        for key in ("title", "description", "alt_text", "tags")
    )
    available = _tokens(metadata)
    overlap = len(target & available)
    relevance = overlap / max(1, len(target))
    ratio = width / height
    role = str(usage_role or "illustration").lower()
    if role in {"cover", "ending"}:
        composition = 1.0 if 0.55 <= ratio <= 0.9 else 0.65 if ratio < 1 else 0.35
    else:
        composition = 1.0 if 0.6 <= ratio <= 1.2 else 0.65
    score = round(relevance * 80 + composition * 20, 4)
    reasons = [
        f"内容关键词匹配 {overlap}/{max(1, len(target))}",
        f"画面比例 {width}:{height}",
    ]
    return score, reasons


def select_visual_assets(
    *,
    candidates: list[dict[str, Any]],
    media_dir: Path,
    topic: str,
    body: str,
    usage_role: str = "illustration",
    limit: int = 8,
    max_image_bytes: int = 12 * 1024 * 1024,
    max_pixels: int = 40_000_000,
    cfg=None,
) -> dict[str, Any]:
    """Verify, gate, de-duplicate and rank authorized image candidates.

    Returns selected entries with verified local paths and evidence, plus explicit
    rejection reasons. Callers must keep these paths tenant-scoped and short-lived.
    """
    if not isinstance(candidates, list):
        raise VisualAssetSelectionError("candidates_must_be_a_list")
    if not 1 <= int(limit) <= 32:
        raise VisualAssetSelectionError("limit_out_of_range")
    root = Path(media_dir).resolve()
    if not root.is_dir():
        raise VisualAssetSelectionError("media_directory_unavailable")

    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for raw_asset in candidates[:256]:
        asset = dict(raw_asset)
        asset_id = asset.get("asset_id", asset.get("id"))
        rejection = _hard_gate_reason(asset)
        if rejection:
            rejected.append({"asset_id": asset_id, "reason": rejection})
            continue
        try:
            path, raw, digest, width, height = _read_verified_image(
                asset, root, max_image_bytes=max_image_bytes, max_pixels=max_pixels,
            )
        except (OSError, VisualAssetSelectionError) as exc:
            rejected.append({"asset_id": asset_id, "reason": str(exc) or "file_read_failed"})
            continue
        if digest in seen_hashes:
            rejected.append({"asset_id": asset_id, "reason": "duplicate_image_content"})
            continue
        seen_hashes.add(digest)
        score, reasons = _score_asset(
            asset, topic=topic, body=body, usage_role=usage_role,
            width=width, height=height,
        )
        verified.append({
            **asset,
            "asset_id": asset_id,
            "verified_path": path,
            "sha256": digest,
            "width": width,
            "height": height,
            "score": score,
            "reasons": reasons,
        })

    vision_order: dict[int, tuple[int, str]] = {}
    if verified and cfg is not None:
        try:
            from .media_semantic import rank_media_with_vision
            ranked = rank_media_with_vision(
                title=topic, body=body, usage_role=usage_role,
                candidates=verified, media_dir=root, cfg=cfg,
            )
            for index, item in enumerate(ranked or []):
                vision_order[int(item["asset_id"])] = (index, str(item.get("reason") or ""))
        except Exception:
            # Optional semantic ranking cannot bypass deterministic safety gates.
            vision_order = {}

    verified.sort(
        key=lambda item: (
            0 if int(item["asset_id"]) in vision_order else 1,
            vision_order.get(int(item["asset_id"]), (10**6, ""))[0],
            -float(item["score"]),
            int(item["asset_id"] or 0),
        )
    )
    selected = verified[:limit]
    for item in selected:
        vision_reason = vision_order.get(int(item["asset_id"]), (0, ""))[1]
        if vision_reason:
            item["reasons"].append(vision_reason)
    for item in verified[limit:]:
        rejected.append({"asset_id": item["asset_id"], "reason": "selection_limit"})
    return {
        "selected": selected,
        "rejected": rejected,
        "candidate_count": len(candidates),
        "verified_count": len(verified),
        "selection_version": "visual-asset-selection-v1",
    }
