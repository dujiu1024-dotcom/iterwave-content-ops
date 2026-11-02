"""AI background revision for typography-first visual cards.

Background generation itself is owned by the main AI-illustration pipeline
(``content_pipeline.render_ai_background`` / ``RoundExecutor._maybe_generate_cover``).
This module only performs a bounded *edit* of an existing background (visual soft-gate
optimisation and single-page revision). Its configuration comes from
``media_generation_settings`` via ``content_pipeline.image_generation_settings`` — the
same single source as the rest of AI illustration; there is no environment switch.

Generated images are decorative backgrounds only. All visible copy remains HTML/CSS
text rendered by Chromium; no generated text is trusted as user-facing content.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def revise_visual_background(
    *, source_path: Path, feedback: list[str], output_dir: Path,
    settings: dict | None, cfg=None,
) -> dict:
    """Apply one bounded edit to an existing background; never generate text.

    ``settings`` is the dict returned by ``image_generation_settings`` (None = disabled).
    Callers must not hold an SQLite write transaction while this runs (network call).
    """
    if not settings:
        return {"status": "disabled", "path": None, "elapsed_ms": 0}
    from .media_generation import generate_image_bytes

    source_path = Path(source_path)
    if not source_path.is_file():
        return {"status": "skipped_missing_source", "path": None, "elapsed_ms": 0}
    mime = _MIME.get(source_path.suffix.lower())
    if not mime:
        return {"status": "skipped_unsupported_source", "path": None, "elapsed_ms": 0}
    model = str(settings.get("model") or "").strip()
    if not model:
        return {"status": "skipped_no_model", "path": None, "elapsed_ms": 0}
    notes = "; ".join(str(item)[:240] for item in feedback[:5]) or "Improve composition and visual clarity"
    prompt = (
        "Edit this existing vertical infographic background according to the art-direction notes. "
        "Preserve the overall subject and topic, improve visual composition, hierarchy support, "
        "color harmony and polish. Keep the central text-safe area calm and low-detail. "
        "Do not add any text, letters, numbers, logos, watermarks, labels or typography. "
        f"Art-direction notes: {notes}"
    )
    prefix = str(settings.get("prefix") or "").strip()
    if prefix:
        prompt = f"{prefix}\n\n{prompt}"
    started = time.monotonic()
    raw, out_mime, out_suffix, width, height = generate_image_bytes(
        prompt, model=model, size=str(settings.get("size") or "1024x1536"),
        source_path=source_path, source_mime_type=mime, cfg=cfg,
    )
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
    return {"status": "revised", "path": str(target), "model": model,
            "mime_type": out_mime, "width": width, "height": height,
            "bytes": len(raw), "elapsed_ms": round((time.monotonic() - started) * 1000)}
