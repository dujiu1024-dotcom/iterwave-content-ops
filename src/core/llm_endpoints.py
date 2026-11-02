"""LLM 主/备端点解析。

所有 LLM 通道均为 OpenAI 兼容 Chat Completions；密钥只从环境变量读取（R23）。
主模型失败时按顺序尝试兜底模型，两者可用不同供应商（如商汤 + 火山方舟）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LlmEndpoint:
    """一次模型调用的完整端点信息。"""

    label: str          # primary / fallback
    provider: str
    base_url: str
    model: str
    api_key: str
    thinking: str = ""  # 仅主模型可能发送；空串 = 请求体不带该字段


def llm_endpoints(cfg: Any) -> list[LlmEndpoint]:
    """按主模型 → 兜底模型顺序返回可用端点；没有密钥的通道直接跳过。"""
    llm = getattr(cfg, "llm", None)
    if not llm or not getattr(llm, "enabled", False):
        return []

    endpoints: list[LlmEndpoint] = []
    primary_key = os.getenv(getattr(llm, "api_key_env", "") or "")
    if getattr(llm, "model", "") and primary_key:
        endpoints.append(LlmEndpoint(
            label="primary",
            provider=str(getattr(llm, "provider", "") or ""),
            base_url=str(getattr(llm, "base_url", "") or ""),
            model=str(getattr(llm, "model", "")),
            api_key=primary_key,
            thinking=str(getattr(llm, "thinking", "") or ""),
        ))

    fallback_key_env = str(getattr(llm, "fallback_api_key_env", "") or "")
    fallback_key = os.getenv(fallback_key_env) if fallback_key_env else None
    fallback_model = str(getattr(llm, "fallback_model", "") or "")
    fallback_base_url = str(getattr(llm, "fallback_base_url", "") or "")
    if fallback_model and fallback_base_url and fallback_key:
        duplicate = any(
            item.base_url.rstrip("/") == fallback_base_url.rstrip("/")
            and item.model == fallback_model
            for item in endpoints
        )
        if not duplicate:
            endpoints.append(LlmEndpoint(
                label="fallback",
                provider=str(getattr(llm, "fallback_provider", "") or ""),
                base_url=fallback_base_url,
                model=fallback_model,
                api_key=fallback_key,
            ))
    return endpoints


def has_llm_endpoint(cfg: Any) -> bool:
    """是否存在至少一个可调用端点（主模型或兜底模型有密钥）。"""
    return bool(llm_endpoints(cfg))


def first_endpoint(cfg: Any) -> Optional[LlmEndpoint]:
    """健康检查/连通性自检使用：优先主模型，主模型缺密钥时退化到兜底。"""
    endpoints = llm_endpoints(cfg)
    return endpoints[0] if endpoints else None
