"""AI background revision with explicit user reasons and whole-set style references."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _reference_montage(target: Path, references: list[str | Path], output_dir: Path) -> Path | None:
    """Create one contact sheet: target page first, adjacent pages as style references."""
    from PIL import Image

    paths = [target, *(Path(item).resolve() for item in references)]
    paths = [path for path in paths if path.is_file()]
    if len(paths) <= 1:
        return None
    cell_w, cell_h = 640, 840
    canvas = Image.new("RGB", (cell_w * 2, cell_h * 2), "white")
    for index, path in enumerate(paths[:4]):
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((cell_w - 24, cell_h - 24))
                cell = Image.new("RGB", (cell_w, cell_h), "white")
                cell.paste(image, ((cell_w - image.width) // 2, (cell_h - image.height) // 2))
                canvas.paste(cell, ((index % 2) * cell_w, (index // 2) * cell_h))
        except (OSError, ValueError):
            continue
    output_dir.mkdir(parents=True, exist_ok=True)
    target_path = output_dir / f".single-page-style-reference-{uuid.uuid4().hex}.png"
    canvas.save(target_path, format="PNG", optimize=True)
    return target_path


def revise_visual_background(
    *, source_path: Path, feedback: list[str], output_dir: Path,
    settings: dict | None, cfg=None, style_baseline: dict | None = None,
    neighbor_context: list[dict] | None = None,
    reference_paths: list[str | Path] | None = None,
    preserve_items: list[str] | None = None,
    forbidden_items: list[str] | None = None, prompt_audit: dict | None = None,
    edit_scope: str = "background",
) -> dict:
    """Apply a bounded edit of one background; keep the user's reason in the final prompt.

    The first panel in the optional reference contact sheet is the target page. Other
    panels are visual references only and must not be copied as page content.
    """
    if not settings:
        return {"status": "disabled", "path": None, "elapsed_ms": 0}
    from .media_generation import generate_image_bytes

    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        return {"status": "skipped_missing_source", "path": None, "elapsed_ms": 0}
    mime = _MIME.get(source_path.suffix.lower())
    if not mime:
        return {"status": "skipped_unsupported_source", "path": None, "elapsed_ms": 0}
    model = str(settings.get("model") or "").strip()
    if not model:
        return {"status": "skipped_no_model", "path": None, "elapsed_ms": 0}
    if edit_scope not in {"background", "full_page"}:
        raise ValueError("不支持的单页视觉编辑范围")
    notes = "; ".join(str(item)[:240] for item in (feedback or []) if str(item).strip())
    if not notes:
        raise ValueError("单张图片重新生成必须提供用户修改理由")
    preserve = preserve_items or [
        "页面中的已确认文字、数字、产品主体和品牌识别元素",
        "页面尺寸、比例、文字安全区和原有信息层级",
        "整套图片的主辅色、背景、字体与插画/摄影风格",
    ]
    forbidden = forbidden_items or [
        "不得改动其他页面或删除整套中的任何页面",
        "不得擅自改写文字、数字、品牌标识或产品主体",
        "不得添加水印、虚构信息或与整套风格冲突的装饰",
    ]
    if edit_scope == "full_page":
        scope_instruction = (
            "Revise the ENTIRE first/target page in the supplied reference sheet. "
            "The remaining panels are style references from adjacent pages; do not copy their text or page layout. "
            "Keep every existing title, sentence, punctuation mark, numeral, logo and product label EXACTLY as shown. "
            "Do not rewrite, translate, remove, abbreviate or invent any text. Do not add new text or watermarks. "
            "You may improve the target page's overall visual composition, color balance, background and illustration "
            "while preserving the original information hierarchy and exact text content."
        )
    else:
        scope_instruction = (
            "Revise ONLY the first/target page background in the supplied reference sheet. "
            "The remaining panels are style references from adjacent pages; do not copy their text or layout. "
            "Preserve the target page's subject, composition intent and text-safe areas. "
            "Do not add any text, letters, numbers, logos, watermarks, labels or typography."
        )
    prompt = (
        scope_instruction + "\n\n"
        f"User's original revision reason (must be followed): {notes}\n"
        "Required preservation:\n- " + "\n- ".join(str(item) for item in preserve) +
        "\nAllowed changes:\n- " + notes +
        "\nForbidden changes:\n- " + "\n- ".join(str(item) for item in forbidden)
    )
    if style_baseline:
        prompt += "\n\nWhole-set style baseline (must inherit):\n" + str(style_baseline)[:5000]
    if neighbor_context:
        prompt += "\n\nAdjacent page continuity notes:\n" + str(neighbor_context)[:4000]
    prefix = str(settings.get("prefix") or "").strip()
    if prefix:
        prompt = f"{prefix}\n\n{prompt}"

    if isinstance(prompt_audit, dict):
        prompt_audit["final_prompt"] = prompt
        prompt_audit["reference_images"] = [str(source_path), *[str(Path(p).resolve()) for p in (reference_paths or [])]]
        prompt_audit["style_baseline"] = style_baseline or {}
        prompt_audit["edit_scope"] = edit_scope
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    montage = _reference_montage(source_path, reference_paths or [], output_dir)
    source_for_edit = montage or source_path
    source_mime = "image/png" if montage else mime
    started = time.monotonic()
    try:
        raw, out_mime, out_suffix, width, height = generate_image_bytes(
            prompt, model=model, size=str(settings.get("size") or "1024x1536"),
            source_path=source_for_edit, source_mime_type=source_mime, cfg=cfg,
        )
    finally:
        if montage:
            montage.unlink(missing_ok=True)
    if out_suffix == ".gif":
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(raw)) as image:
            converted = BytesIO()
            image.convert("RGB").save(converted, format="PNG")
            raw, out_suffix, out_mime = converted.getvalue(), ".png", "image/png"
    target = output_dir / f"visual_bg_revision_{uuid.uuid4().hex[:12]}{out_suffix}"
    target.write_bytes(raw)
    return {
        "status": "revised", "path": str(target), "model": model,
        "mime_type": out_mime, "width": width, "height": height,
        "bytes": len(raw), "elapsed_ms": round((time.monotonic() - started) * 1000),
        "final_prompt": prompt, "edit_scope": edit_scope,
        "reference_images": [str(source_path), *[str(Path(p).resolve()) for p in (reference_paths or [])]],
    }
