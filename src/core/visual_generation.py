"""Bounded AI background revision for typography-first visual cards."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def revise_visual_background(
    *, source_path: Path, feedback: list[str], output_dir: Path,
    settings: dict | None, cfg=None, style_baseline: dict | None = None,
    neighbor_context: list[dict] | None = None, reference_paths: list[Path] | None = None,
    preserve_items: list[str] | None = None, forbidden_items: list[str] | None = None,
    prompt_audit: dict | None = None,
) -> dict:
    """Revise one existing background with explicit user direction and auditable context.

    The temporary source copy is removed after the provider call. Visible text is never
    delegated to image generation; all copy remains rendered by the application.
    """
    reasons = [str(item).strip() for item in (feedback or []) if str(item).strip()]
    if not reasons:
        raise ValueError("必须提供用户修改理由")
    if not settings:
        return {"status": "disabled", "path": None, "elapsed_ms": 0}

    source_path = Path(source_path)
    if not source_path.is_file():
        return {"status": "skipped_missing_source", "path": None, "elapsed_ms": 0}
    mime = _MIME.get(source_path.suffix.lower())
    if not mime:
        return {"status": "skipped_unsupported_source", "path": None, "elapsed_ms": 0}
    model = str(settings.get("model") or "").strip()
    if not model:
        return {"status": "skipped_no_model", "path": None, "elapsed_ms": 0}

    baseline = style_baseline if isinstance(style_baseline, dict) else {}
    neighbors = neighbor_context if isinstance(neighbor_context, list) else []
    references = [Path(p) for p in (reference_paths or []) if Path(p).is_file()]
    preserve = [str(x).strip() for x in (preserve_items or []) if str(x).strip()]
    forbidden = [str(x).strip() for x in (forbidden_items or []) if str(x).strip()]
    notes = "; ".join(reasons[:5])
    prompt_parts = [
        "Edit this existing vertical infographic background according to the user's revision reason.",
        "Preserve the overall subject and topic; improve composition, hierarchy support, color harmony and polish.",
        "Keep the central text-safe area calm and low-detail.",
        "Do not add any text, letters, numbers, logos, watermarks, labels or typography.",
        f"User revision reason: {notes}",
    ]
    if baseline:
        prompt_parts.append("Brand style baseline (follow consistently): " + json.dumps(baseline, ensure_ascii=False, sort_keys=True))
    if neighbors:
        prompt_parts.append("Neighboring page visual context: " + json.dumps(neighbors[:8], ensure_ascii=False, sort_keys=True))
    if preserve:
        prompt_parts.append("Items that must be preserved: " + "; ".join(preserve[:20]))
    if forbidden:
        prompt_parts.append("Forbidden changes: " + "; ".join(forbidden[:20]))
    if references:
        prompt_parts.append("Use the supplied neighboring reference images for visual continuity.")
    prefix = str(settings.get("prefix") or "").strip()
    prompt = "\n".join(([prefix] if prefix else []) + prompt_parts)
    audit_refs = [str(p) for p in references]
    started = time.monotonic()
    from .media_generation import generate_image_bytes

    # Some providers mutate or consume the source file. Isolate that behavior from
    # the original asset and guarantee temporary cleanup on both success and failure.
    temp_dir = Path(tempfile.mkdtemp(prefix="iterwave-visual-revision-"))
    temp_source = temp_dir / ("source" + source_path.suffix.lower())
    shutil.copy2(source_path, temp_source)
    try:
        raw, out_mime, out_suffix, width, height = generate_image_bytes(
            prompt, model=model, size=str(settings.get("size") or "1024x1536"),
            source_path=temp_source, source_mime_type=mime, cfg=cfg,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if out_suffix == ".gif":
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(raw)) as image:
            converted = BytesIO()
            image.convert("RGB").save(converted, format="PNG")
            raw, out_suffix, out_mime = converted.getvalue(), ".png", "image/png"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"visual_bg_revision_{uuid.uuid4().hex[:12]}{out_suffix}"
    target.write_bytes(raw)
    elapsed = round((time.monotonic() - started) * 1000)
    result = {"status": "revised", "path": str(target), "model": model,
              "mime_type": out_mime, "width": width, "height": height,
              "bytes": len(raw), "elapsed_ms": elapsed, "final_prompt": prompt}
    if prompt_audit is not None:
        prompt_audit.update({
            "final_prompt": prompt,
            "reference_images": audit_refs,
            "style_baseline": baseline,
            "neighbor_context": neighbors,
            "preserve_items": preserve,
            "forbidden_items": forbidden,
        })
    return result
