"""Platform writing-style API routes."""

from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field


class PlatformStyleUpdate(BaseModel):
    """平台文案风格：只提交改动字段。"""

    title_style: str | None = Field(None, max_length=200)
    body_style: str | None = Field(None, max_length=200)
    tone: str | None = Field(None, max_length=200)
    hashtags: list[str] | None = Field(None, max_length=10)


def build_platform_styles_router(
    get_context: Callable[[], dict],
    now_provider: Callable,
    platform_enum,
    http_exception=HTTPException,
    *,
    route_class: type[APIRoute] = APIRoute,
) -> APIRouter:
    """Build platform style routes with explicit runtime dependencies."""
    router = APIRouter(route_class=route_class)

    @router.get("/api/platform_styles")
    def list_platform_styles():
        conn = get_context()["conn"]
        rows = conn.execute(
            "SELECT platform, title_style, body_style, tone, hashtags, "
            "updated_at, updated_by FROM platform_styles ORDER BY platform"
        ).fetchall()
        items = []
        for row in rows:
            try:
                tags = json.loads(row["hashtags"] or "[]")
            except (TypeError, ValueError):
                tags = []
            items.append({
                "platform": row["platform"],
                "title_style": row["title_style"],
                "body_style": row["body_style"],
                "tone": row["tone"],
                "hashtags": tags if isinstance(tags, list) else [],
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            })
        return {"items": items}

    @router.patch("/api/platform_styles/{platform}")
    def update_platform_style(platform: str, req: PlatformStyleUpdate):
        """改平台风格 / 话题标签：立即生效，逐字段写 config_changes 审计。"""
        conn = get_context()["conn"]
        if platform not in {item.value for item in platform_enum}:
            raise http_exception(400, "platform 只能是 xhs / douyin / kuaishou / wechat_channels")
        row = conn.execute(
            "SELECT title_style, body_style, tone, hashtags FROM platform_styles "
            "WHERE platform = ?",
            (platform,),
        ).fetchone()
        if row is None:
            raise http_exception(
                404, f"{platform} 尚无平台风格记录（请确认迁移 016 已执行）"
            )

        new_values: dict[str, str] = {}
        for field in ("title_style", "body_style", "tone"):
            value = getattr(req, field)
            if value is None:
                continue
            text = value.strip()
            if not text:
                raise http_exception(422, f"{field} 不能为空")
            new_values[field] = text
        if req.hashtags is not None:
            tags: list[str] = []
            for raw in req.hashtags:
                tag = str(raw).strip().lstrip("#")
                if tag and tag not in tags:
                    tags.append(tag)
            new_values["hashtags"] = json.dumps(tags, ensure_ascii=False)

        old_values = {
            "title_style": row["title_style"],
            "body_style": row["body_style"],
            "tone": row["tone"],
            "hashtags": row["hashtags"] or "[]",
        }
        changed = [
            (key, old_values[key], value)
            for key, value in new_values.items()
            if old_values[key] != value
        ]
        if not changed:
            return {"platform": platform, "changed": 0, "message": "没有改动"}

        now = now_provider().isoformat(timespec="seconds")
        for key, old, new in changed:
            conn.execute(
                "INSERT INTO config_changes (key, old_value, new_value, changed_at) "
                "VALUES (?, ?, ?, ?)",
                (f"platform_styles.{platform}.{key}", str(old), str(new), now),
            )
        assignments = ", ".join(f"{key} = ?" for key in new_values)
        conn.execute(
            f"UPDATE platform_styles SET {assignments}, updated_at = ?, updated_by = ? "
            "WHERE platform = ?",
            (*new_values.values(), now, "settings_ui", platform),
        )
        conn.commit()
        return {
            "platform": platform,
            "changed": len(changed),
            "fields": {key: new for key, _old, new in changed},
        }

    return router
