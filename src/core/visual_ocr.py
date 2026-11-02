"""Deterministic OCR gate for rendered Chinese visual cards.

OCR is a noisy measurement, not an exact text extraction engine. The gate therefore
uses normalized character coverage plus ordered similarity, while keeping numeric
content strict. Missing OCR tooling or language data is never treated as a pass.
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path


def normalize_ocr_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return "".join(
        ch for ch in value
        if ch.isalnum() or ch == "%" or 0x3400 <= ord(ch) <= 0x9fff
    )


def compare_expected_text(expected: str, recognized: str) -> dict:
    expected_norm = normalize_ocr_text(expected)
    recognized_norm = normalize_ocr_text(recognized)
    if not expected_norm:
        return {
            "passed": True, "expected_chars": 0, "matched_chars": 0,
            "recall": 1.0, "sequence_similarity": 1.0, "missing_numbers": [],
        }

    # Counter coverage tolerates OCR line-order changes without allowing repeated
    # characters to be counted more often than they occur in the source.
    expected_counts = Counter(expected_norm)
    recognized_counts = Counter(recognized_norm)
    matched = sum(min(count, recognized_counts[char]) for char, count in expected_counts.items())
    recall = matched / len(expected_norm)
    sequence_similarity = SequenceMatcher(
        None, expected_norm, recognized_norm, autojunk=False,
    ).ratio()

    # OCR often drops decimal separators and percent signs. Preserve every numeric
    # digit token exactly, but do not reject solely because punctuation was missed.
    expected_numbers = re.findall(r"\d+", expected_norm)
    recognized_numbers = Counter(re.findall(r"\d+", recognized_norm))
    required_numbers = Counter(expected_numbers)
    missing_numbers = [
        number for number, count in required_numbers.items()
        for _ in range(max(0, count - recognized_numbers[number]))
    ]

    return {
        "passed": recall >= 0.86 and sequence_similarity >= 0.72 and not missing_numbers,
        "expected_chars": len(expected_norm),
        "matched_chars": matched,
        "recall": round(recall, 4),
        "sequence_similarity": round(sequence_similarity, 4),
        "missing_numbers": missing_numbers,
    }


def run_ocr(
    image_path: Path, *, language: str = "chi_sim+eng",
    timeout: int = 45, page_segmentation_mode: int = 6,
) -> dict:
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "reason": "tesseract_not_installed", "text": ""}
    with tempfile.TemporaryDirectory(prefix="iterwave-ocr-") as temp_dir:
        output = Path(temp_dir) / "recognized"
        command = [
            executable, str(image_path), str(output), "-l", language,
            "--oem", "1", "--psm", str(page_segmentation_mode),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "unavailable", "reason": "ocr_timeout", "text": ""}
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "")[-500:]
            return {
                "status": "unavailable", "reason": "ocr_execution_failed",
                "detail": detail, "text": "",
            }
        txt_path = output.with_suffix(".txt")
        if not txt_path.exists():
            return {"status": "unavailable", "reason": "ocr_output_missing", "text": ""}
        return {
            "status": "available",
            "text": txt_path.read_text(encoding="utf-8", errors="replace"),
        }


def validate_rendered_pages(images: list[Path], expected_texts: list[str], report_path: Path) -> dict:
    if len(images) != len(expected_texts):
        raise ValueError("OCR 校验输入页数与预期文案页数不一致")
    results = []
    unavailable = None
    for image_path, expected in zip(images, expected_texts):
        first_pass = run_ocr(Path(image_path), page_segmentation_mode=6)
        if first_pass["status"] != "available":
            results.append({
                "image": Path(image_path).name,
                "status": "unavailable",
                "reason": first_pass.get("reason"),
            })
            unavailable = first_pass
            continue
        comparison = compare_expected_text(expected, first_pass["text"])
        passes = 1
        # Only pay for the alternate layout strategy when the normal block mode fails.
        if not comparison["passed"]:
            alternate = run_ocr(Path(image_path), page_segmentation_mode=11)
            if alternate["status"] == "available":
                alternate_comparison = compare_expected_text(expected, alternate["text"])
                passes += 1
                if (
                    alternate_comparison["recall"],
                    alternate_comparison["sequence_similarity"],
                ) > (
                    comparison["recall"],
                    comparison["sequence_similarity"],
                ):
                    comparison = alternate_comparison
        comparison["ocr_passes"] = passes
        results.append({
            "image": Path(image_path).name,
            "status": "passed" if comparison["passed"] else "failed",
            **comparison,
        })
    if unavailable:
        status = "unavailable"
    elif all(item["status"] == "passed" for item in results):
        status = "passed"
    else:
        status = "failed"
    report = {
        "status": status,
        "pages": results,
        "gate": "not_passed" if status != "passed" else "passed",
    }
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if status == "failed":
        failed_pages = [
            {
                "image": item["image"],
                "recall": item.get("recall"),
                "sequence_similarity": item.get("sequence_similarity"),
                "missing_numbers": item.get("missing_numbers", []),
            }
            for item in results if item["status"] == "failed"
        ]
        detail = json.dumps(failed_pages, ensure_ascii=False, separators=(",", ":"))
        raise ValueError(f"OCR 文字校验失败，视觉结果未通过硬门；识别指标={detail}")
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
    return report if isinstance(report, dict) else {
        "status": "unavailable", "reason": "invalid_ocr_report", "gate": "not_passed",
    }
