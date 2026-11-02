"""Opt-in AI background generation for typography-first visual cards.

Generated images are decorative backgrounds only. All visible copy remains HTML/CSS
text rendered by Chromium; no generated text is trusted as user-facing content.
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_visual_background(
    *, title: str, topic: str, output_dir: Path, cfg=None,
) -> dict:
    """Generate one validated background when explicitly enabled; otherwise skip."""
    if os.getenv("ITERWAVE_ENABLE_AI_VISUALS", "").strip() != "1":
        return {"status": "disabled", "path": None, "model": None, "elapsed_ms": 0}
    key_env = os.getenv("ITERWAVE_IMAGE_API_KEY_ENV", "OPENAI_API_KEY").strip()
    if not key_env or not os.getenv(key_env):
        return {"status": "skipped_no_key", "path": None, "model": None, "elapsed_ms": 0}

    import time
    from .media_generation import generate_image_bytes

    model = os.getenv("ITERWAVE_IMAGE_MODEL", "gpt-image-1").strip()
    if not model:
        return {"status": "skipped_no_model", "path": None, "model": None, "elapsed_ms": 0}
    safe_topic = str(topic or "")[:180]
    safe_title = str(title or "")[:100]
    prompt = (
        "Create a refined vertical editorial illustration/background for a Chinese social-media "
        "infographic. This is a background asset only: absolutely no text, letters, numbers, "
        "logos, watermarks, labels, UI, or typography. Keep the central 65 percent visually calm "
        "and low-detail so HTML text can be placed over it with high contrast. Use tasteful "
        "visual metaphors, layered depth, restrained color, and professional art direction. "
        f"Topic context (reference only, not text to render): {safe_topic}. "
        f"Content headline context: {safe_title}."
    )
    started = time.monotonic()
    raw, mime, suffix, width, height = generate_image_bytes(
        prompt, model=model, size="1024x1536", cfg=cfg,
    )
    if suffix == ".gif":
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(raw)) as image:
            converted = BytesIO()
            image.convert("RGB").save(converted, format="PNG")
            raw, suffix, mime = converted.getvalue(), ".png", "image/png"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"visual_bg_{uuid.uuid4().hex[:12]}{suffix}"
    path.write_bytes(raw)
    return {
        "status": "generated",
        "path": str(path),
        "model": model,
        "mime_type": mime,
        "width": width,
        "height": height,
        "bytes": len(raw),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def revise_visual_background(
    *, source_path: Path, feedback: list[str], output_dir: Path, cfg=None,
) -> dict:
    """Apply one bounded edit to an existing background; never generate text."""
    if os.getenv("ITERWAVE_ENABLE_AI_VISUALS", "").strip() != "1":
        return {"status": "disabled", "path": None, "elapsed_ms": 0}
    from .media_generation import generate_image_bytes
    import time

    source_path = Path(source_path)
    if not source_path.is_file():
        return {"status": "skipped_missing_source", "path": None, "elapsed_ms": 0}
    key_env = os.getenv("ITERWAVE_IMAGE_API_KEY_ENV", "OPENAI_API_KEY").strip()
    if not key_env or not os.getenv(key_env):
        return {"status": "skipped_no_key", "path": None, "elapsed_ms": 0}
    model = os.getenv("ITERWAVE_IMAGE_MODEL", "gpt-image-1").strip()
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
    started = time.monotonic()
    suffix = source_path.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
    if not mime:
        return {"status": "skipped_unsupported_source", "path": None, "elapsed_ms": 0}
    raw, out_mime, out_suffix, width, height = generate_image_bytes(
        prompt, model=model, size="1024x1536", source_path=source_path,
        source_mime_type=mime, cfg=cfg,
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
            "bytes": len(raw), "elapsed_ms": round((time.monotonic()-started)*1000)}
