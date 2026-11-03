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


_NUMBER_RUN = re.compile(r"\d+(?:\s?[.,，]\s?\d+)*")


def _number_tokens(value: str) -> list[str]:
    """Digit runs split at real separators (space, colon, newline), with decimal and
    thousands separators removed, so "1.8 : 1" -> ["18", "1"] and adjacent list items
    never merge into one token."""
    text = unicodedata.normalize("NFKC", value or "")
    return [re.sub(r"[^0-9]", "", token) for token in _NUMBER_RUN.findall(text)]


def compare_expected_text(expected: str, recognized: str,
                          recognized_numbers: list[str] | None = None) -> dict:
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
    expected_numbers = _number_tokens(expected)
    recognized_numbers = Counter(
        _number_tokens(recognized) if recognized_numbers is None else recognized_numbers
    )
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
    extra_config: tuple[str, ...] = (),
) -> dict:
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "reason": "tesseract_not_installed", "text": ""}
    with tempfile.TemporaryDirectory(prefix="iterwave-ocr-") as temp_dir:
        output = Path(temp_dir) / "recognized"
        command = [
            executable, str(Path(image_path).resolve()), str(output), "-l", language,
            "--oem", "1", "--psm", str(page_segmentation_mode),
        ]
        for item in extra_config:
            command += ["-c", item]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
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


def _is_light(css_color: str) -> bool:
    nums = [float(n) for n in re.findall(r"[\d.]+", css_color or "")[:3]]
    if len(nums) < 3:
        return False
    r, g, b = (n / 255 for n in nums)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5


def _css_gray(css_color: str) -> float | None:
    nums = [float(n) for n in re.findall(r"[\d.]+", css_color or "")[:3]]
    if len(nums) < 3:
        return None
    r, g, b = nums
    return 0.299 * r + 0.587 * g + 0.114 * b


def _median_gray(image) -> int:
    histogram = image.histogram()[:256]
    half, seen = sum(histogram) / 2, 0
    for value, count in enumerate(histogram):
        seen += count
        if seen >= half:
            return value
    return 255


def _ocr_crop(gray, region: dict, *, glyph_px: int, pad: int) -> dict:
    """Crop one DOM text region, binarise it to black text on white using the exact text
    colour reported by the DOM (handles light-on-dark pills), then scale to a target
    glyph height before OCR."""
    from PIL import Image, ImageOps

    # Content box grown by ``pad`` but never past the element's own border box, so a
    # pill's rounded corners (page background) never enter the crop.
    bx, by = float(region.get("bx", region["x"] - pad)), float(region.get("by", region["y"] - pad))
    bw = float(region.get("bw", region["w"] + 2 * pad))
    bh = float(region.get("bh", region["h"] + 2 * pad))
    left = max(0, int(max(bx, region["x"] - pad)))
    top = max(0, int(max(by, region["y"] - pad)))
    right = min(gray.width, int(min(bx + bw, region["x"] + region["w"] + pad)))
    bottom = min(gray.height, int(min(by + bh, region["y"] + region["h"] + pad)))
    crop = gray.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
    text_gray = _css_gray(region.get("color", ""))
    if text_gray is not None:
        median = _median_gray(crop)
        threshold = max(30.0, abs(median - text_gray) / 2)
        crop = crop.point(lambda value: 0 if abs(value - text_gray) < threshold else 255)
    elif _is_light(region.get("color", "")):
        crop = ImageOps.invert(crop)
    scale = glyph_px / max(1.0, float(region.get("font_size") or glyph_px))
    if abs(scale - 1) > 0.05:
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                           Image.LANCZOS)
    crop = ImageOps.expand(crop, border=24, fill=255)
    lines = round(float(region["h"]) / max(1.0, float(region.get("line_height") or 1)))
    with tempfile.TemporaryDirectory(prefix="iterwave-ocr-") as temp_dir:
        crop_path = Path(temp_dir) / "region.png"
        crop.save(crop_path)
        chars = len(normalize_ocr_text(region.get("text", "")))
        mode = 10 if chars == 1 else 8 if chars == 2 else (7 if lines <= 1 else 6)
        return run_ocr(crop_path, page_segmentation_mode=mode)


def ocr_numbers(image_path: Path, numbers: list[dict]) -> dict:
    """Read every rendered numeric token box on its own (digits-only whitelist).

    Returns the digit strings actually recognised; a token whose digits are not read
    exactly is simply absent, so the caller's strict numeric check fails for it."""
    from PIL import Image, ImageOps

    recognized: list[str] = []
    with Image.open(image_path) as source:
        gray = source.convert("L")
        for box in numbers:
            want = re.sub(r"[^0-9]", "", box.get("text", ""))
            got = ""
            for glyph_px in (40, 56, 32):
                left = max(0, int(box["x"]))
                top = max(0, int(box["y"]) - 2)
                right = min(gray.width, int(box["x"] + box["w"]) + 1)
                bottom = min(gray.height, int(box["y"] + box["h"]) + 2)
                crop = gray.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
                text_gray = _css_gray(box.get("color", ""))
                if text_gray is not None:
                    median = _median_gray(crop)
                    threshold = max(30.0, abs(median - text_gray) / 2)
                    crop = crop.point(lambda v: 0 if abs(v - text_gray) < threshold else 255)
                scale = glyph_px / max(1.0, float(box.get("font_size") or glyph_px))
                crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                                   Image.LANCZOS)
                crop = ImageOps.expand(crop, border=24, fill=255)
                with tempfile.TemporaryDirectory(prefix="iterwave-ocr-") as temp_dir:
                    crop_path = Path(temp_dir) / "number.png"
                    crop.save(crop_path)
                    result = run_ocr(
                        crop_path, language="eng",
                        page_segmentation_mode=10 if len(want) == 1 else 8 if len(want) == 2 else 7,
                        extra_config=("tessedit_char_whitelist=0123456789.,%+-",),
                    )
                if result["status"] != "available":
                    return result
                got = re.sub(r"[^0-9]", "", result["text"])
                if got == want:
                    break
            if got:
                recognized.append(got)
    return {"status": "available", "numbers": recognized}


# Each region is read at up to three target glyph heights; chi_sim is sensitive to glyph
# size, and one genuine reading of the pixels that matches the region text suffices.
_OCR_ATTEMPTS = ((40, 10), (48, 12), (32, 8))


def ocr_regions(image_path: Path, regions: list[dict]) -> dict:
    """OCR each DOM text region separately instead of the whole page.

    Whole-page psm 6 recognition of a designed card (large display title, light text on
    accent pills, mixed sizes) recalls only 20-40% of correct covers. Region OCR keeps the
    gate strict (same thresholds, numbers exact) without that false-negative noise.
    """
    from PIL import Image

    texts = []
    with Image.open(image_path) as source:
        gray = source.convert("L")
        for region in regions:
            best_text, best_key = "", (-1.0, -1.0)
            for glyph_px, pad in _OCR_ATTEMPTS:
                result = _ocr_crop(gray, region, glyph_px=glyph_px, pad=pad)
                if result["status"] != "available":
                    return result
                comparison = compare_expected_text(region.get("text", ""), result["text"])
                key = (comparison["recall"], comparison["sequence_similarity"])
                if key > best_key:
                    best_text, best_key = result["text"], key
                if comparison["passed"]:
                    break
            texts.append(best_text.strip())
    return {"status": "available", "text": "\n".join(texts), "mode": "regions"}


def _whole_page_ocr(image_path: Path, expected: str) -> tuple[dict, int]:
    first_pass = run_ocr(Path(image_path), page_segmentation_mode=6)
    if first_pass["status"] != "available":
        return first_pass, 1
    comparison = compare_expected_text(expected, first_pass["text"])
    if comparison["passed"]:
        return first_pass, 1
    alternate = run_ocr(Path(image_path), page_segmentation_mode=11)
    if alternate["status"] != "available":
        return first_pass, 2
    alt = compare_expected_text(expected, alternate["text"])
    better = (alt["recall"], alt["sequence_similarity"]) > (
        comparison["recall"], comparison["sequence_similarity"])
    return (alternate if better else first_pass), 2


def validate_rendered_pages(images: list[Path], expected_texts: list[str], report_path: Path,
                            regions: list[list[dict]] | None = None,
                            numbers: list[list[dict]] | None = None) -> dict:
    """Run the OCR hard gate. With ``regions`` (DOM text boxes per page) each region is read
    separately; without them the legacy whole-page strategy is used."""
    if len(images) != len(expected_texts) or (regions is not None and len(regions) != len(images)) \
            or (numbers is not None and len(numbers) != len(images)):
        raise ValueError("OCR 校验输入页数与预期文案页数不一致")
    from concurrent.futures import ThreadPoolExecutor

    def _recognize(index: int) -> tuple[dict, int]:
        if regions is not None:
            result = ocr_regions(Path(images[index]), regions[index])
            if result["status"] == "available" and numbers is not None:
                read = ocr_numbers(Path(images[index]), numbers[index])
                if read["status"] != "available":
                    return read, 1
                result["numbers"] = read["numbers"]
            return result, 1
        return _whole_page_ocr(Path(images[index]), expected_texts[index])

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(images)))) as pool:
        recognized = list(pool.map(_recognize, range(len(images))))
    results = []
    unavailable = None
    for image_path, expected, (ocr, passes) in zip(images, expected_texts, recognized):
        if ocr["status"] != "available":
            results.append({
                "image": Path(image_path).name,
                "status": "unavailable",
                "reason": ocr.get("reason"),
            })
            unavailable = ocr
            continue
        comparison = compare_expected_text(expected, ocr["text"], ocr.get("numbers"))
        comparison["ocr_passes"] = passes
        comparison["ocr_mode"] = ocr.get("mode", "page")
        comparison["recognized_text"] = (ocr.get("text") or "")[:2000]
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
