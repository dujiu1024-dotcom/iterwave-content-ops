"""Canonical, immutable input snapshots for Activation-to-production handoff."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


class ActivationSampleSnapshotError(ValueError):
    """Raised when an Activation sample cannot be frozen or verified safely."""


def _as_mapping(sample: Mapping[str, Any]) -> dict[str, Any]:
    # sqlite3.Row supports keys/index access but is not a dict and has no .get().
    if hasattr(sample, "keys"):
        return {key: sample[key] for key in sample.keys()}
    return dict(sample)


def canonical_activation_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact copy fields that are allowed to enter formal production."""
    sample = _as_mapping(sample)
    title = sample.get("title")
    body = sample.get("body")
    if not isinstance(title, str) or not title.strip():
        raise ActivationSampleSnapshotError("Activation 样稿标题为空")
    if not isinstance(body, str) or not body.strip():
        raise ActivationSampleSnapshotError("Activation 样稿正文为空")
    raw_tags = sample.get("hashtags_json", sample.get("tags", []))
    if isinstance(raw_tags, str):
        try:
            tags = json.loads(raw_tags)
        except (TypeError, ValueError) as exc:
            raise ActivationSampleSnapshotError("Activation 样稿标签 JSON 无效") from exc
    else:
        tags = raw_tags
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ActivationSampleSnapshotError("Activation 样稿标签必须是字符串数组")
    return {"title": title, "body": body, "tags": tags}


def activation_sample_content_hash(sample: Mapping[str, Any]) -> str:
    canonical = canonical_activation_sample(sample)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_activation_sample_snapshot(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze sample identity, tenant, content version, and exact copy bytes."""
    sample = _as_mapping(sample)
    sample_id = str(sample.get("id") or "").strip()
    tenant_id = str(sample.get("tenant_id") or "").strip()
    platform = str(sample.get("platform") or "").strip()
    version = sample.get("content_version")
    if not sample_id or not tenant_id or not platform:
        raise ActivationSampleSnapshotError("Activation 样稿缺少可信身份或平台")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ActivationSampleSnapshotError("Activation 样稿版本无效")
    copy = canonical_activation_sample(sample)
    return {
        "sample_content_id": sample_id,
        "tenant_id": tenant_id,
        "platform": platform,
        "content_version": version,
        "content_hash": activation_sample_content_hash(copy),
        **copy,
    }


def verify_activation_sample_snapshot(
    current_sample: Mapping[str, Any], snapshot: Mapping[str, Any],
) -> None:
    """Fail closed if tenant, platform, version, or any copy field changed."""
    current = build_activation_sample_snapshot(current_sample)
    for field in (
        "sample_content_id", "tenant_id", "platform", "content_version", "content_hash",
    ):
        if current[field] != snapshot.get(field):
            raise ActivationSampleSnapshotError(
                f"Activation 样稿在排队后发生变化：{field} 不匹配"
            )
