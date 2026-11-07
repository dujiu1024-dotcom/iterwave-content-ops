"""Deterministic, fail-closed quality gate for complete AI visual page sets."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Sequence

from PIL import Image, UnidentifiedImageError


def validate_visual_group(
    images: Sequence[Path],
    *,
    roles: Sequence[str],
    expected_texts: Sequence[str],
    ocr_report: dict,
    require_ending: bool = True,
    max_page_bytes: int = 12 * 1024 * 1024,
    min_dimension: int = 256,
) -> dict:
    """Validate page completeness, role order, image integrity, consistency and OCR."""
    paths = [Path(item) for item in images]
    role_list = [str(item or "").strip().lower() for item in roles]
    expected = [str(item or "").strip() for item in expected_texts]
    failures: list[dict] = []

    if not paths:
        failures.append({"code": "empty_group"})
    if len(paths) != len(role_list) or len(paths) != len(expected):
        failures.append({"code": "page_count_mismatch"})
    if role_list:
        wanted = ["cover", *(["body"] * max(0, len(role_list) - 2)), "ending"] if require_ending else role_list
        if require_ending and role_list != wanted:
            failures.append({"code": "page_roles_invalid", "actual": role_list})
        elif not require_ending and (role_list[0] != "cover" or any(x != "body" for x in role_list[1:])):
            failures.append({"code": "page_roles_invalid", "actual": role_list})
    if any(not item for item in expected):
        failures.append({"code": "page_text_missing"})

    page_records: list[dict] = []
    hashes: set[str] = set()
    dimensions: list[tuple[int, int]] = []
    for index, path in enumerate(paths):
        record = {"page_index": index, "role": role_list[index] if index < len(role_list) else None}
        if path.is_symlink() or not path.is_file():
            failures.append({"code": "page_file_missing_or_unsafe", "page_index": index})
            page_records.append({**record, "status": "failed"})
            continue
        try:
            size = path.stat().st_size
            if size <= 0 or size > max_page_bytes:
                failures.append({"code": "page_size_invalid", "page_index": index, "bytes": size})
                page_records.append({**record, "status": "failed", "bytes": size})
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in hashes:
                failures.append({"code": "duplicate_page_image", "page_index": index})
            hashes.add(digest)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                image_format = str(image.format or "").upper()
            if min(width, height) < min_dimension or image_format not in {"PNG", "JPEG", "WEBP"}:
                failures.append({
                    "code": "page_image_spec_invalid", "page_index": index,
                    "width": width, "height": height, "format": image_format,
                })
            dimensions.append((width, height))
            page_records.append({
                **record, "status": "passed", "bytes": size,
                "width": width, "height": height, "format": image_format,
                "sha256": digest,
            })
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            failures.append({"code": "page_image_decode_failed", "page_index": index, "reason": str(exc)[:160]})
            page_records.append({**record, "status": "failed"})

    if dimensions:
        ratios = [width / height for width, height in dimensions]
        if max(ratios) - min(ratios) > 0.03:
            failures.append({"code": "page_aspect_ratio_inconsistent", "ratios": [round(x, 4) for x in ratios]})

    ocr_pages = ocr_report.get("pages") if isinstance(ocr_report, dict) else None
    if not isinstance(ocr_report, dict) or ocr_report.get("status") != "passed" or ocr_report.get("gate") != "passed":
        failures.append({"code": "group_ocr_gate_not_passed"})
    if not isinstance(ocr_pages, list) or len(ocr_pages) != len(paths):
        failures.append({"code": "ocr_page_count_mismatch"})
    elif any(not isinstance(page, dict) or page.get("status") != "passed" for page in ocr_pages):
        failures.append({"code": "ocr_page_failed"})

    normalized_texts = [re.sub(r"\s+", "", item).casefold() for item in expected if item]
    if len(normalized_texts) != len(set(normalized_texts)):
        failures.append({"code": "duplicate_page_text"})

    return {
        "version": "visual-group-quality-v1",
        "status": "failed" if failures else "passed",
        "gate": "not_passed" if failures else "passed",
        "page_count": len(paths),
        "roles": role_list,
        "pages": page_records,
        "checks": {
            "role_sequence": not any(item["code"] == "page_roles_invalid" for item in failures),
            "image_integrity": not any(item["code"] in {
                "page_file_missing_or_unsafe", "page_size_invalid",
                "page_image_spec_invalid", "page_image_decode_failed",
            } for item in failures),
            "cross_page_consistency": not any(item["code"] in {
                "duplicate_page_image", "page_aspect_ratio_inconsistent", "duplicate_page_text",
            } for item in failures),
            "ocr": not any(item["code"].startswith("ocr_") or item["code"] == "group_ocr_gate_not_passed" for item in failures),
        },
        "failures": failures,
    }
