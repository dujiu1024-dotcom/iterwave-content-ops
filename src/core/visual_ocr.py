"""Deterministic OCR gate for rendered Chinese visual cards.

OCR is a noisy measurement, not an exact text extraction engine. The gate therefore
uses normalized character coverage plus ordered similarity, while keeping numeric
content strict. Missing OCR tooling or language data is never treated as a pass.
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path


def normalize_ocr_text(value: str) -> str:
    """Normalize width/case and ignore punctuation, whitespace and line breaks."""
    value = unicodedata.normalize("NFKC", value or "").lower()
    return "".join(
        ch for ch in value
        if ch.isalnum() or ch == "%" or 0x3400 <= ord(ch) <= 0x9fff
    )


_OCR_RULES_VERSION = "ocr-semantic-v2"
_NEGATIONS = ("禁止", "不", "没", "无", "非", "未")
_SEGMENT_SPLIT = re.compile(r"[。！？!?；;，,、：:\n]+")
_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)(?!\d)")
_NUMERIC_PATTERN = re.compile(
    r"(?<!\d)[+-]?\d+(?:[.,，]\d+)?\s*(?:%|％|万人|亿人|万次|亿次|万家|万件|万元|亿元|元|万|亿|年|月|日|天|小时|分钟|秒|人|个|次|倍|件|台|家|岁|℃|°c)?"
)

_NUMBER_BOX_PATTERN = re.compile(
    r"(?<![0-9,.])([+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?%?)(?![0-9])"
)


def _expected_number_box_tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKC", value or "")
    return [
        token
        for match in _NUMBER_BOX_PATTERN.finditer(text)
        for token in _numeric_tokens(match.group(1))
    ]



def _numeric_tokens(value: str) -> list[str]:
    """Keep decimal/date/percentage/unit semantics and occurrence counts."""
    text = unicodedata.normalize("NFKC", value or "").lower()
    dates = []
    for match in _DATE_PATTERN.finditer(text):
        year, month, day = (int(part) for part in re.findall(r"\d+", match.group(1)))
        dates.append((match.start(), match.end(), f"{year:04d}-{month:02d}-{day:02d}"))
    tokens: list[tuple[int, int, str]] = list(dates)
    for match in _NUMERIC_PATTERN.finditer(text):
        if any(match.start() < end and match.end() > begin for begin, end, _ in dates):
            continue
        token = re.sub(r"\s+", "", match.group(0))
        if token.startswith("+"):
            token = token[1:]
        tokens.append((match.start(), match.end(), token))
    return [item[2] for item in sorted(tokens)]


def _segments(value: str) -> list[str]:
    return [normalize_ocr_text(part) for part in _SEGMENT_SPLIT.split(unicodedata.normalize("NFKC", value or "")) if normalize_ocr_text(part)]


def _negation_counts(value: str) -> Counter:
    text = normalize_ocr_text(value)
    return Counter({word: text.count(word) for word in _NEGATIONS if word in text})


def _negations_match_by_segment(expected: str, recognized: str) -> tuple[bool, list[dict]]:
    expected_parts, recognized_parts = _segments(expected), _segments(recognized)
    failures = []
    if len(expected_parts) != len(recognized_parts):
        if any(_negation_counts(part) for part in expected_parts + recognized_parts):
            failures.append({
                "segment": None, "word": "alignment_uncertain",
                "required": [dict(_negation_counts(part)) for part in expected_parts],
                "recognized": [dict(_negation_counts(part)) for part in recognized_parts],
            })
        return not failures, failures
    for index, (expected_part, recognized_part) in enumerate(zip(expected_parts, recognized_parts)):
        required = _negation_counts(expected_part)
        actual = _negation_counts(recognized_part)
        for word in sorted(set(required) | set(actual)):
            if actual[word] != required[word]:
                failures.append({
                    "segment": index, "word": word,
                    "required": required[word], "recognized": actual[word],
                })
    return not failures, failures


def compare_expected_text(
    expected: str, recognized: str,
    recognized_numbers: list[str] | None = None,
    *, critical: bool = False,
    expected_number_boxes: list[str] | None = None,
) -> dict:
    expected_norm = normalize_ocr_text(expected)
    recognized_norm = normalize_ocr_text(recognized)
    if not expected_norm:
        return {
            "passed": False, "expected_chars": 0, "recognized_chars": len(recognized_norm),
            "matched_chars": 0, "recall": 0.0, "sequence_similarity": 0.0,
            "length_ratio": 0.0, "missing_numbers": [], "extra_numbers": [],
            "number_mismatch": False, "negation_mismatches": [], "critical": critical,
        }

    expected_counts = Counter(expected_norm)
    recognized_counts = Counter(recognized_norm)
    matched = sum(min(count, recognized_counts[char]) for char, count in expected_counts.items())
    recall = matched / len(expected_norm)
    sequence_similarity = SequenceMatcher(None, expected_norm, recognized_norm, autojunk=False).ratio()
    length_ratio = len(recognized_norm) / max(1, len(expected_norm))

    expected_numbers = Counter(_numeric_tokens(expected))
    text_numbers = Counter(_numeric_tokens(recognized))
    missing_numbers: list[str] = []
    extra_numbers: list[str] = []
    if recognized_numbers is not None and expected_number_boxes is not None:
        expected_boxes = Counter(token for value in expected_number_boxes for token in _numeric_tokens(value))
        actual_boxes = Counter(recognized_numbers)
        missing_numbers.extend(
            f"box:{token}" for token, count in expected_boxes.items()
            for _ in range(max(0, count - actual_boxes[token]))
        )
        extra_numbers.extend(
            f"box:{token}" for token, count in actual_boxes.items()
            for _ in range(max(0, count - expected_boxes[token]))
        )

        def _parts(token: str) -> tuple[str, str]:
            match = re.match(r"^([+-]?\d+(?:[.,]\d+)?)(.*)$", token)
            return (match.group(1), match.group(2)) if match else (token, "")

        # Region mode has exhaustive DOM numeric boxes; full-text OCR can hallucinate
        # punctuation-adjacent digits (e.g. "8.7%" as "8.709"). Numeric values are
        # therefore gated by the exact crops, while prose OCR is used to verify units.
        expected_units = Counter(unit for token in expected_numbers.elements() for unit in [_parts(token)[1]] if unit)
        text_units = Counter(unit for token in text_numbers.elements() for unit in [_parts(token)[1]] if unit)
        box_units = Counter(unit for token in actual_boxes.elements() for unit in [_parts(token)[1]] if unit)
        recognized_units = Counter({
            unit: max(text_units[unit], box_units[unit])
            for unit in set(text_units) | set(box_units)
        })
        for unit, count in expected_units.items():
            if recognized_units[unit] < count:
                missing_numbers.extend(f"unit:{unit}" for _ in range(count - recognized_units[unit]))
        for unit, count in recognized_units.items():
            if count > expected_units[unit]:
                extra_numbers.extend(f"unit:{unit}" for _ in range(count - expected_units[unit]))
    else:
        actual_numbers = Counter(text_numbers if recognized_numbers is None else recognized_numbers)
        missing_numbers.extend(
            token for token, count in expected_numbers.items()
            for _ in range(max(0, count - actual_numbers[token]))
        )
        extra_numbers.extend(
            token for token, count in actual_numbers.items()
            for _ in range(max(0, count - expected_numbers[token]))
        )
    negations_ok, negation_mismatches = _negations_match_by_segment(expected, recognized)

    ordinary_text_ok = recall >= 0.86 and sequence_similarity >= 0.72
    if critical:
        ordinary_text_ok = expected_norm == recognized_norm
    passed = ordinary_text_ok and length_ratio <= 1.2 and not missing_numbers and not extra_numbers and negations_ok
    return {
        "passed": passed, "expected_chars": len(expected_norm),
        "recognized_chars": len(recognized_norm), "matched_chars": matched,
        "recall": round(recall, 4),
        "sequence_similarity": round(sequence_similarity, 4),
        "length_ratio": round(length_ratio, 4),
        "missing_numbers": missing_numbers, "extra_numbers": extra_numbers,
        "number_mismatch": bool(missing_numbers or extra_numbers),
        "negation_mismatches": negation_mismatches, "critical": critical,
    }



def preflight_ocr(*, languages: str = "chi_sim+eng", timeout: int = 45) -> dict:
    """Check Tesseract, permissions, language packs and timeout before paid image calls."""
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "reason": "tesseract_not_installed"}
    executable_path = Path(executable)
    if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
        return {"status": "unavailable", "reason": "tesseract_not_executable"}
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 300:
        return {"status": "unavailable", "reason": "ocr_timeout_invalid"}
    try:
        from .image_generation_budget import remaining_budget_seconds
        remaining = remaining_budget_seconds()
    except RuntimeError:
        return {"status": "unavailable", "reason": "image_generation_hard_timeout"}
    if remaining is not None:
        if remaining < 1:
            return {"status": "unavailable", "reason": "image_generation_hard_timeout"}
        timeout = min(timeout, int(remaining))
    required = list(dict.fromkeys(["chi_sim", "eng", *[x.strip() for x in str(languages or "").split("+") if x.strip()]]))
    try:
        version = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True, timeout=min(timeout, 10))
        remaining = remaining_budget_seconds()
        if remaining is not None:
            if remaining < 1:
                return {"status": "unavailable", "reason": "image_generation_hard_timeout"}
            timeout = min(timeout, int(remaining))
        listed = subprocess.run([executable, "--list-langs"], check=True, capture_output=True, text=True, timeout=min(timeout, 15))
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "reason": "ocr_preflight_timeout"}
    except (OSError, subprocess.CalledProcessError):
        return {"status": "unavailable", "reason": "tesseract_not_executable"}
    available = {line.strip() for line in (listed.stdout or "").splitlines() if line.strip() and not line.lower().startswith("list of")}
    missing = [language for language in required if language not in available]
    if missing:
        return {"status": "unavailable", "reason": "ocr_language_missing", "missing_languages": missing}
    return {"status": "available", "executable": executable, "languages": required, "version": (version.stdout or "").splitlines()[0][:120], "timeout_seconds": timeout}


def run_ocr(
    image_path: Path, *, language: str | None = None,
    timeout: float | int | None = None, page_segmentation_mode: int = 6,
    extra_config: tuple[str, ...] = (),
) -> dict:
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "reason": "tesseract_not_installed", "text": ""}
    try:
        from .task_context import current_config
        cfg = current_config()
        if cfg is None:
            from .config import get_config
            cfg = get_config()
    except Exception:
        cfg = None
    if language is None:
        language = str(getattr(getattr(cfg, "media_generation", None), "ocr_languages", "chi_sim+eng"))
    if timeout is None:
        timeout = int(getattr(getattr(cfg, "media_generation", None), "ocr_timeout_seconds", 45))
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 300:
        return {"status": "unavailable", "reason": "ocr_timeout_invalid", "text": ""}
    try:
        from .image_generation_budget import remaining_budget_seconds
        remaining = remaining_budget_seconds()
    except RuntimeError:
        return {"status": "unavailable", "reason": "image_generation_hard_timeout", "text": ""}
    if remaining is not None:
        if remaining <= 0:
            return {"status": "unavailable", "reason": "image_generation_hard_timeout", "text": ""}
        timeout = min(float(timeout), remaining)
    with tempfile.TemporaryDirectory(prefix="iterwave-ocr-") as temp_dir:
        output = Path(temp_dir) / "recognized"
        command = [executable, str(image_path), str(output), "-l", language, "--oem", "1", "--psm", str(page_segmentation_mode)]
        for item in extra_config:
            command += ["-c", item]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "unavailable", "reason": "ocr_timeout", "text": ""}
        except subprocess.CalledProcessError as exc:
            return {"status": "unavailable", "reason": "ocr_execution_failed", "detail": (exc.stderr or "")[-500:], "text": ""}
        txt_path = output.with_suffix(".txt")
        if not txt_path.exists():
            return {"status": "unavailable", "reason": "ocr_output_missing", "text": ""}
        return {"status": "available", "text": txt_path.read_text(encoding="utf-8", errors="replace")}


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
    """Read each numeric box and preserve decimal/percent/unit tokens, not digits alone."""
    from PIL import Image, ImageOps

    recognized: list[str] = []
    with Image.open(image_path) as source:
        gray = source.convert("L")
        for box in numbers:
            expected_tokens = _numeric_tokens(box.get("text", ""))
            expected_digits = re.sub(r"[^0-9]", "", box.get("text", ""))
            best_tokens: list[str] = []
            best_digits = ""
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
                        page_segmentation_mode=10 if len(expected_digits) == 1 else 8 if len(expected_digits) == 2 else 7,
                        extra_config=("tessedit_char_whitelist=0123456789.,%+-",),
                    )
                if result["status"] != "available":
                    return result
                best_digits = re.sub(r"[^0-9]", "", result["text"])
                best_tokens = _numeric_tokens(result["text"])
                if best_digits == expected_digits and best_tokens == expected_tokens:
                    break
            if best_digits == expected_digits and best_tokens == expected_tokens:
                recognized.extend(best_tokens)
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
        return alternate, 2
    alt = compare_expected_text(expected, alternate["text"])
    better = (alt["recall"], alt["sequence_similarity"]) > (
        comparison["recall"], comparison["sequence_similarity"])
    return (alternate if better else first_pass), 2


def validate_rendered_pages(images: list[Path], expected_texts: list[str], report_path: Path,
                            regions: list[list[dict]] | None = None,
                            numbers: list[list[dict]] | None = None,
                            critical_pages: list[bool] | None = None) -> dict:
    """Run the OCR hard gate. With regions each DOM text box is read separately."""
    if (
        len(images) != len(expected_texts)
        or (regions is not None and len(regions) != len(images))
        or (numbers is not None and len(numbers) != len(images))
        or (critical_pages is not None and len(critical_pages) != len(images))
    ):
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
    for index, (image_path, expected, (ocr, passes)) in enumerate(zip(images, expected_texts, recognized)):
        if ocr["status"] != "available":
            results.append({
                "image": Path(image_path).name,
                "status": "unavailable",
                "reason": ocr.get("reason"),
            })
            unavailable = ocr
            continue
        comparison = compare_expected_text(
            expected, ocr["text"], ocr.get("numbers"),
            critical=bool(critical_pages[index]) if critical_pages is not None else False,
            expected_number_boxes=_expected_number_box_tokens(expected) if ocr.get("numbers") is not None else None,
        )
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
                "length_ratio": item.get("length_ratio"),
                "missing_numbers": item.get("missing_numbers", []),
                "extra_numbers": item.get("extra_numbers", []),
                "negation_mismatches": item.get("negation_mismatches", []),
                "critical": item.get("critical", False),
            }
            for item in results if item["status"] == "failed"
        ]
        detail = json.dumps(failed_pages, ensure_ascii=False, separators=(",", ":"))
        raise ValueError(f"OCR 粗检未通过，视觉结果未通过硬门；识别指标={detail}")
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
