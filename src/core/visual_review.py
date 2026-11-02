"""Optional fail-closed six-dimension visual review."""
from __future__ import annotations
import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx

DIMENSIONS = ("composition", "typography", "color", "hierarchy", "relevance", "polish")

def _not_evaluated(reason: str) -> dict:
    return {"status": "not_evaluated", "reason": reason, "score": None, "dimensions": {}}

def parse_visual_review(payload: object) -> dict:
    if not isinstance(payload, dict):
        return _not_evaluated("invalid_response")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return _not_evaluated("missing_choices")
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if isinstance(text, list):
        text = "".join(str(x.get("text", "")) for x in text if isinstance(x, dict))
    try:
        result = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return _not_evaluated("invalid_json")
    raw = result.get("dimensions") if isinstance(result, dict) else None
    if not isinstance(raw, dict):
        return _not_evaluated("missing_dimensions")
    if any(not isinstance(raw.get(k), (int, float)) or isinstance(raw.get(k), bool) or not 0 <= raw[k] <= 100 for k in DIMENSIONS):
        return _not_evaluated("invalid_dimension_scores")
    scores = {k: round(float(raw[k]), 1) for k in DIMENSIONS}
    feedback = result.get("feedback", [])
    if not isinstance(feedback, list):
        feedback = []
    return {"status": "evaluated", "score": round(sum(scores.values()) / 6, 1),
            "dimensions": scores, "feedback": [str(x)[:300] for x in feedback[:8]]}

def assess_visual_pages(images: list[Path]) -> dict:
    if os.getenv("ITERWAVE_ENABLE_VISUAL_REVIEW") != "1":
        return _not_evaluated("disabled")
    base = os.getenv("ITERWAVE_VISUAL_REVIEW_API_BASE_URL", "").strip().rstrip("/")
    key_name = os.getenv("ITERWAVE_VISUAL_REVIEW_API_KEY_ENV", "OPENAI_API_KEY").strip()
    key, model = os.getenv(key_name), os.getenv("ITERWAVE_VISUAL_REVIEW_MODEL", "").strip()
    if not base or not key or not model:
        return _not_evaluated("missing_configuration")
    try:
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return _not_evaluated("invalid_endpoint")
    except ValueError:
        return _not_evaluated("invalid_endpoint")
    if not images:
        return _not_evaluated("no_images")
    selected = list(dict.fromkeys([Path(images[0]), Path(images[len(images)//2]), Path(images[-1])]))
    parts = [{"type": "text", "text": "Rate these Chinese infographic pages on composition, typography, color, hierarchy, topic relevance, and polish. Return JSON with dimensions containing all six integer scores from 0 to 100, plus feedback array. Do not assess factual accuracy."}]
    total = 0
    try:
        for path in selected:
            data = path.read_bytes()
            total += len(data)
            if total > 12 * 1024 * 1024:
                return _not_evaluated("payload_too_large")
            mime = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".webp":"image/webp"}.get(path.suffix.lower())
            if not mime:
                return _not_evaluated("unsupported_image")
            parts.append({"type":"image_url", "image_url":{"url":"data:"+mime+";base64,"+base64.b64encode(data).decode("ascii")}})
        started = time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(90, connect=10), follow_redirects=False) as client:
            response = client.post(base + "/chat/completions",
                headers={"Authorization":"Bearer "+key},
                json={"model":model,"temperature":0,"response_format":{"type":"json_object"},
                      "messages":[{"role":"user","content":parts}]})
            response.raise_for_status()
            result = parse_visual_review(response.json())
        if result["status"] == "evaluated":
            result["model"] = model
            result["elapsed_ms"] = round((time.monotonic()-started)*1000)
        return result
    except (OSError, httpx.HTTPError, ValueError):
        return _not_evaluated("request_failed")
