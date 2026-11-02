"""Pure response helpers shared by FastAPI route modules."""

from __future__ import annotations

from datetime import datetime, timezone


_VALIDATION_FIELD_LABELS = {
    "platform": "平台标识",
    "usage_role": "图片用途",
    "limit": "数量",
    "tenant_id": "账号组标识",
    "name": "名称",
    "path": "文件位置",
    "email": "邮箱",
    "url": "链接",
    "content_id": "内容标识",
}


def api_datetime(value, *, naive_timezone=timezone.utc):
    """Add an explicit timezone to legacy naive values; preserve explicit offsets."""
    if not value or not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.isoformat()


def friendly_validation_messages(errors: list[dict]) -> list[str]:
    """Map Pydantic validation errors to stable Chinese messages without echoing raw input."""
    messages: list[str] = []
    for error in errors:
        loc = error.get("loc") or ()
        field = str(loc[-1]) if loc else ""
        label = _VALIDATION_FIELD_LABELS.get(field, "请求参数")
        kind = str(error.get("type") or "")
        ctx = error.get("ctx") or {}
        if kind == "missing":
            reason = "不能为空"
        elif kind in ("string_pattern_mismatch", "string_type"):
            reason = "格式不符合要求"
        elif kind in ("int_parsing", "int_type", "float_parsing", "float_type"):
            reason = "类型或格式不正确"
        elif kind in ("greater_than_equal", "greater_than"):
            bound = ctx.get("ge", ctx.get("gt", "允许范围"))
            reason = f"不能小于 {bound}" if "ge" in ctx else f"必须大于 {bound}"
        elif kind in ("less_than_equal", "less_than"):
            bound = ctx.get("le", ctx.get("lt", "允许范围"))
            reason = f"不能大于 {bound}" if "le" in ctx else f"必须小于 {bound}"
        elif kind == "string_too_short":
            reason = "长度不足"
        elif kind == "string_too_long":
            reason = "长度超出限制"
        else:
            reason = "内容不符合要求"
        messages.append(f"{label}：{reason}")
    return messages or ["请求参数不符合要求"]
