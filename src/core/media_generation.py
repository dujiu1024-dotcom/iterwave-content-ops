"""OpenAI-compatible image generation/editing adapter.

图片创作通道与对话模型（llm）完全独立：主模型（商汤 U1.5）+ 兜底模型（火山 Seedream），
两者可用不同供应商，按顺序尝试。

Provider URL and API key are supplied by server environment, never by the request body.
Only base64 image responses are accepted; provider-returned arbitrary URLs are not fetched.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from src.core.redact import redact

logger = logging.getLogger(__name__)


_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}


class ImageGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class _ImageEndpoint:
    """一次图片生成的完整端点信息（主模型或兜底模型）。"""

    label: str
    provider: str
    base_url: str
    model: str
    api_key: str
    watermark: bool | None = None


def _resolve_config(cfg=None):
    """Prefer the tenant config bound to the current background task."""
    if cfg is not None:
        return cfg
    from src.core.task_context import current_config
    cfg = current_config()
    if cfg is None:
        from src.core.config import get_config
        cfg = get_config()
    return cfg


def validate_image_bytes(raw: bytes, cfg=None) -> tuple[str, str, int, int]:
    cfg = _resolve_config(cfg)
    policy = cfg.media_generation
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ImageGenerationError("生成结果不是有效的图片文件或超过安全像素限制") from exc
    if image_format not in _FORMATS:
        raise ImageGenerationError("生成结果格式不支持，仅接受 JPEG、PNG、WebP、GIF")
    if width < 1 or height < 1 or width > min(policy.max_dimension, 20000) or height > min(policy.max_dimension, 20000) or width * height > min(policy.max_pixels, 40_000_000):
        raise ImageGenerationError("生成结果尺寸超出允许范围（总像素不超过 4000 万）")
    mime_type, suffix = _FORMATS[image_format]
    return mime_type, suffix, width, height



def _target_dimensions(size: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", str(size or ""))
    if not match:
        raise ImageGenerationError("图片目标尺寸必须明确为 WIDTHxHEIGHT，拒绝跳过尺寸校验")
    width, height = int(match.group(1)), int(match.group(2))
    if not (256 <= width <= 20000 and 256 <= height <= 20000):
        raise ImageGenerationError("图片目标宽高超出允许范围")
    return width, height


def validate_target_image_spec(width: int, height: int, size: str, cfg=None) -> None:
    """Reject generated images outside the requested dimensions and configured aspect band."""
    target = _target_dimensions(size)
    if target is None:
        return
    target_width, target_height = target
    tolerance = 0.03
    width_tolerance = max(8, round(target_width * tolerance))
    height_tolerance = max(8, round(target_height * tolerance))
    if abs(width - target_width) > width_tolerance or abs(height - target_height) > height_tolerance:
        raise ImageGenerationError(
            f"图片尺寸不符合目标规格：实际 {width}x{height}，目标 {target_width}x{target_height}，允许误差 3%"
        )
    ratio = width / height
    quality = getattr(cfg, "media_quality", None) if cfg is not None else None
    if quality is not None:
        target_ratio = target_width / target_height
        if target_ratio < 1:
            lower = float(getattr(quality, "portrait_ratio_min", 0.55))
            upper = float(getattr(quality, "portrait_ratio_max", 0.85))
            if not lower <= ratio <= upper:
                raise ImageGenerationError(
                    f"图片纵横比 {ratio:.4f} 超出竖图配置范围 {lower:.2f}-{upper:.2f}"
                )
        elif target_ratio <= float(getattr(quality, "square_ratio_max", 1.1)):
            if ratio > float(getattr(quality, "square_ratio_max", 1.1)):
                raise ImageGenerationError("图片纵横比超出方图配置范围")
        elif ratio > float(getattr(quality, "landscape_ratio_max", 1.7)):
            raise ImageGenerationError("图片纵横比超出横图配置范围")
    actual_ratio = width / height
    if abs(actual_ratio - target_width / target_height) > max(0.015, (target_width / target_height) * 0.02):
        raise ImageGenerationError("图片实际纵横比与目标纵横比不一致")



def _safe_base_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme in ("https", "http")
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _watermark(value) -> bool | None:
    return value if isinstance(value, bool) else None


def _endpoints(cfg, model: str, allow_fallback: bool | None = None) -> list[_ImageEndpoint]:
    """按主模型 → 兜底模型顺序返回可用图片端点；缺密钥/地址的通道直接跳过。"""
    policy = cfg.media_generation

    def attr(name, default=None):
        # 兼容测试与旧配置：字段缺失时按默认值处理
        return getattr(policy, name, default)

    base_url = os.environ.get(
        "ITERWAVE_IMAGE_API_BASE_URL", str(attr("base_url", "") or "")
    ).strip().rstrip("/")
    key_env = os.environ.get(
        "ITERWAVE_IMAGE_API_KEY_ENV", str(attr("api_key_env", "OPENAI_API_KEY") or "")
    ).strip()
    primary_model = (
        model
        or os.environ.get("ITERWAVE_IMAGE_MODEL")
        or str(attr("model", "") or "")
    ).strip()
    endpoints: list[_ImageEndpoint] = []

    primary_key = os.getenv(key_env) if key_env else None
    if not (base_url and primary_model and primary_key):
        # 主通道未配置时直接报配置错误，不静默降级到兜底通道
        return []
    endpoints.append(_ImageEndpoint(
        label="primary",
        provider=str(attr("provider", "") or ""),
        base_url=base_url,
        model=primary_model,
        api_key=primary_key,
        watermark=_watermark(attr("watermark", None)),
    ))

    if allow_fallback is None:
        allow_fallback = bool(attr("fallback_enabled", True))
    if not allow_fallback:
        return endpoints

    fallback_base_url = str(attr("fallback_base_url", "") or "").strip().rstrip("/")
    fallback_model = str(attr("fallback_model", "") or "").strip()
    fallback_key_env = str(attr("fallback_api_key_env", "") or "").strip()
    fallback_key = os.getenv(fallback_key_env) if fallback_key_env else None
    duplicate = any(
        item.base_url == fallback_base_url and item.model == fallback_model
        for item in endpoints
    )
    if fallback_base_url and fallback_model and fallback_key and not duplicate:
        endpoints.append(_ImageEndpoint(
            label="fallback",
            provider=str(attr("fallback_provider", "") or ""),
            base_url=fallback_base_url,
            model=fallback_model,
            api_key=fallback_key,
            watermark=_watermark(attr("watermark", None)),
        ))
    return endpoints


def _is_sensenova(endpoint: _ImageEndpoint) -> bool:
    return "sensenova" in endpoint.provider.lower() or "token.sensenova.cn" in endpoint.base_url


def _error_message(response: httpx.Response) -> str:
    message = ""
    try:
        payload = response.json()
        message = str(payload.get("error", {}).get("message") or payload.get("message") or "")
    except (ValueError, AttributeError):
        message = response.text[:300]
    return f"图片服务返回 HTTP {response.status_code}: {redact(message[:300])}"


def _decode_payload(payload, cfg) -> tuple[bytes, str, str, int, int]:
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ImageGenerationError("图片服务响应缺少 data[0]")
    encoded = items[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise ImageGenerationError(
            "图片服务没有返回 b64_json；请使用支持 base64 图片响应的兼容接口"
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ImageGenerationError("图片服务返回的 base64 无效") from exc
    policy = cfg.media_generation
    max_bytes = int(getattr(policy, "max_response_bytes", 20 * 1024 * 1024))
    if not raw or len(raw) > min(max_bytes, 20 * 1024 * 1024):
        raise ImageGenerationError("图片服务返回内容为空或超过 20MB")
    mime_type, suffix, width, height = validate_image_bytes(raw, cfg=cfg)
    return raw, mime_type, suffix, width, height


def _bounded_post(client: httpx.Client, url: str, *, max_bytes: int, **kwargs) -> httpx.Response:
    """Read an image-provider response incrementally and stop before buffering an oversized body."""
    with client.stream("POST", url, **kwargs) as streamed:
        declared = streamed.headers.get("content-length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise ImageGenerationError("图片服务响应 JSON 超过允许大小")
            except ValueError:
                pass
        body = bytearray()
        for chunk in streamed.iter_bytes():
            if len(body) + len(chunk) > max_bytes:
                raise ImageGenerationError("图片服务响应 JSON 超过允许大小")
            body.extend(chunk)
        return httpx.Response(
            status_code=streamed.status_code,
            headers=streamed.headers,
            content=bytes(body),
            request=streamed.request,
        )


def _request_endpoint(
    endpoint: _ImageEndpoint,
    prompt: str,
    size: str,
    *,
    cfg,
    source_path: Path | None,
    source_mime_type: str | None,
    source_filename: str | None,
    budget_timeout_seconds: float | None = None,
) -> tuple[bytes, str, str, int, int]:
    policy = cfg.media_generation
    timeout_seconds = float(getattr(policy, "timeout_seconds", 150))
    if budget_timeout_seconds is not None:
        timeout_seconds = max(0.1, min(timeout_seconds, float(budget_timeout_seconds)))
    connect_timeout = min(float(getattr(policy, "connect_timeout_seconds", 15)), timeout_seconds)
    max_response_bytes = int(getattr(policy, "max_response_bytes", 20 * 1024 * 1024))
    max_encoded_bytes = ((max_response_bytes + 2) // 3) * 4 + 1024 * 1024
    headers = {"Authorization": f"Bearer {endpoint.api_key}"}
    sensenova = _is_sensenova(endpoint)
    data: dict = {"model": endpoint.model, "prompt": prompt, "size": size, "n": 1}
    if sensenova and endpoint.watermark is not None:
        # 商汤默认加水印（watermark=true），业务图必须显式关闭
        data["watermark"] = endpoint.watermark
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout),
            follow_redirects=False,
        ) as client:
            if source_path:
                source_bytes = source_path.read_bytes()
                if sensenova:
                    mime_type = source_mime_type or "image/png"
                    data_url = f"data:{mime_type};base64,{base64.b64encode(source_bytes).decode()}"
                    body = {
                        **data,
                        "response_format": "b64_json",
                        "images": [{"image_url": data_url}],
                    }
                    response = _bounded_post(
                        client, endpoint.base_url + "/images/edits",
                        max_bytes=max_encoded_bytes, headers=headers, json=body,
                    )
                else:
                    files = {
                        "image": (
                            source_filename or source_path.name,
                            source_bytes,
                            source_mime_type or "application/octet-stream",
                        )
                    }
                    response = _bounded_post(
                        client, endpoint.base_url + "/images/edits",
                        max_bytes=max_encoded_bytes,
                        headers=headers,
                        data={key: str(value) for key, value in data.items()},
                        files=files,
                    )
            else:
                # 显式要求 base64 响应（部分供应商默认返回临时 URL，本适配器只接受 b64_json）
                response = _bounded_post(
                    client, endpoint.base_url + "/images/generations",
                    max_bytes=max_encoded_bytes,
                    headers=headers,
                    json={**data, "response_format": "b64_json"},
                )
        if response.status_code >= 400:
            raise ImageGenerationError(_error_message(response))
        max_response_bytes = int(getattr(policy, "max_response_bytes", 20 * 1024 * 1024))
        max_encoded_bytes = ((max_response_bytes + 2) // 3) * 4 + 1024 * 1024
        if len(response.content) > max_encoded_bytes:
            raise ImageGenerationError("图片服务响应 JSON 超过允许大小")
        payload = response.json()
    except ImageGenerationError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ImageGenerationError(f"图片服务请求失败：{redact(str(exc))}") from None
    return _decode_payload(payload, cfg)


def generate_image_bytes(
    prompt: str,
    *,
    model: str,
    size: str,
    source_path: Path | None = None,
    source_mime_type: str | None = None,
    source_filename: str | None = None,
    cfg=None,
    allow_fallback: bool | None = None,
) -> tuple[bytes, str, str, int, int]:
    """按主模型 → 兜底模型顺序调用图片生成/编辑接口，返回校验后的图片与元数据。

    allow_fallback=None 时按配置默认值；False 表示只用主模型，失败直接抛错。
    """
    cfg = _resolve_config(cfg)
    from .image_generation_budget import article_budget, has_active_article, has_active_run, image_generation_run
    if not has_active_run():
        policy = cfg.media_generation
        with image_generation_run(max_calls=int(getattr(policy, "max_api_calls_per_run", 30)), timeout_seconds=int(getattr(policy, "hard_timeout_seconds", 300)), max_concurrency=int(getattr(policy, "max_concurrency", 2))):
            with article_budget(max_calls=int(getattr(policy, "max_api_calls_per_article", 27)), page_max_calls=int(getattr(policy, "max_api_calls_per_page", 3))):
                return generate_image_bytes(prompt, model=model, size=size, source_path=source_path, source_mime_type=source_mime_type, source_filename=source_filename, cfg=cfg, allow_fallback=allow_fallback)
    if not has_active_article():
        policy = cfg.media_generation
        with article_budget(max_calls=int(getattr(policy, "max_api_calls_per_article", 27)), page_max_calls=int(getattr(policy, "max_api_calls_per_page", 3))):
            return generate_image_bytes(prompt, model=model, size=size, source_path=source_path, source_mime_type=source_mime_type, source_filename=source_filename, cfg=cfg, allow_fallback=allow_fallback)
    from .visual_ocr import preflight_ocr
    ocr_policy = cfg.media_generation
    ocr_check = preflight_ocr(languages=getattr(ocr_policy, "ocr_languages", "chi_sim+eng"), timeout=getattr(ocr_policy, "ocr_timeout_seconds", 45))
    if ocr_check.get("status") != "available":
        raise ImageGenerationError(f"OCR 前置预检失败（{ocr_check.get('reason')}），已阻止图片 API 调用")
    endpoints = _endpoints(cfg, model, allow_fallback=allow_fallback)
    if not endpoints:
        raise ImageGenerationError(
            "未配置图片生成服务密钥。请检查服务端部署环境中的密钥配置."
        )

    errors: list[str] = []
    for index, endpoint in enumerate(endpoints):
        if not _safe_base_url(endpoint.base_url):
            if endpoint.label == "primary":
                raise ImageGenerationError("图片 API 地址必须是无认证信息的 HTTP(S) URL")
            errors.append(f"{endpoint.label} {endpoint.model}: 图片 API 地址无效")
            continue
        try:
            from .image_generation_budget import provider_request
            with provider_request() as remaining_seconds:
                result = _request_endpoint(
                    endpoint, prompt, size, cfg=cfg, source_path=source_path,
                    source_mime_type=source_mime_type, source_filename=source_filename,
                    budget_timeout_seconds=remaining_seconds,
                )
            validate_target_image_spec(result[3], result[4], size, cfg=cfg)
            return result
        except ImageGenerationError as exc:
            errors.append(f"{endpoint.label} {endpoint.model}: {exc}")
            if index < len(endpoints) - 1:
                logger.warning(
                    "图片生成失败，切换兜底通道 label=%s model=%s error=%s",
                    endpoint.label, endpoint.model, redact(str(exc)),
                )
    raise ImageGenerationError("图片生成失败：" + "；".join(errors))
