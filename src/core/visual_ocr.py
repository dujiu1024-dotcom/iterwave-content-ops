"""Deterministic OCR gate for rendered Chinese visual cards.

The gate never treats missing OCR tooling or missing Chinese trained data as a pass.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path


def normalize_ocr_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return "".join(ch for ch in value if ch.isalnum() or ch == "%" or "\u3400" <= ch <= "\u9fff")


def compare_expected_text(expected: str, recognized: str) -> dict:
    expected_norm = normalize_ocr_text(expected)
    recognized_norm = normalize_ocr_text(recognized)
    if not expected_norm:
        return {"passed": True, "expected_chars": 0, "matched_chars": 0, "recall": 1.0}
    # Subsequence recall tolerates OCR-inserted punctuation/spacing but not dropped copy.
    cursor = 0
    matched = 0
    for char in expected_norm:
        found = recognized_norm.find(char, cursor)
        if found >= 0:
            matched += 1
            cursor = found + 1
    recall = matched / len(expected_norm)
    # Numbers are critical: every expected numeric token must appear exactly.
    expected_numbers = re.findall(r"\d+(?:\.\d+)?%?", expected_norm)
    recognized_numbers = re.findall(r"\d+(?:\.\d+)?%?", recognized_norm)
    missing_numbers = [n for n in expected_numbers if n not in recognized_numbers]
    return {
        "passed": recall >= 0.90 and not missing_numbers,
        "expected_chars": len(expected_norm),
        "matched_chars": matched,
        "recall": round(recall, 4),
        "missing_numbers": missing_numbers,
    }


def run_ocr(image_path: Path, *, language: str = "chi_sim+eng", timeout: int = 45) -> dict:
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "reason": "tesseract_not_installed", "text": ""}
    with tempfile.TemporaryDirectory(prefix="iterwave-ocr-") as temp_dir:
        output = Path(temp_dir) / "recognized"
        command = [executable, str(image_path), str(output), "-l", language, "--psm", "6"]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "unavailable", "reason": "ocr_timeout", "text": ""}
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "")[-500:]
            return {"status": "unavailable", "reason": "ocr_execution_failed", "detail": detail, "text": ""}
        txt_path = output.with_suffix(".txt")
        if not txt_path.exists():
            return {"status": "unavailable", "reason": "ocr_output_missing", "text": ""}
        return {"status": "available", "text": txt_path.read_text(encoding="utf-8", errors="replace")}


def validate_rendered_pages(images: list[Path], expected_texts: list[str], report_path: Path) -> dict:
    if len(images) != len(expected_texts):
        raise ValueError("OCR 校验输入页数与预期文案页数不一致")
    results = []
    unavailable = None
    for image_path, expected in zip(images, expected_texts):
        ocr = run_ocr(Path(image_path))
        if ocr["status"] != "available":
            unavailable = ocr
            results.append({"image": Path(image_path).name, "status": "unavailable", "reason": ocr.get("reason")})
            continue
        comparison = compare_expected_text(expected, ocr["text"])
        results.append({"image": Path(image_path).name, "status": "passed" if comparison["passed"] else "failed", **comparison})
    if unavailable:
        status = "unavailable"
    elif all(item["status"] == "passed" for item in results):
        status = "passed"
    else:
        status = "failed"
    report = {"status": status, "pages": results, "gate": "not_passed" if status != "passed" else "passed"}
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if status == "failed":
        raise ValueError("OCR 文字校验失败，视觉结果未通过硬门")
    return report


def load_ocr_report(images: list[Path]) -> dict:
    """Load the OCR gate report associated with a rendered card set."""
    if not images:
        return {"status": "unavailable", "reason": "no_rendered_images", "gate": "not_passed"}
    report_path = Path(images[0]).parent / f"visual_ocr_{Path(images[0]).stem}.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "unavailable", "reason": "ocr_report_missing", "gate": "not_passed"}
    return report if isinstance(report, dict) else {"status": "unavailable", "reason": "invalid_ocr_report", "gate": "not_passed"}
