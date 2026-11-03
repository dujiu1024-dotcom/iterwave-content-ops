"""Tenant-scoped operations targets and industry preset API routes."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException


def build_tenant_settings_router(
    get_context: Callable[[], dict],
    patch_config: Callable,
    config_not_allowed_error: type[Exception],
    config_patch_error: type[Exception],
) -> APIRouter:
    """Build settings APIs with explicit tenant context and config patch dependencies."""
    router = APIRouter()

    @router.get("/api/ops-targets")
    def get_ops_targets():
        from src.core.ops_targets_repository import read_targets
        return {"items": read_targets(get_context()["conn"])}

    @router.put("/api/ops-targets")
    def put_ops_targets(request: dict):
        from src.core.ops_targets_repository import load_ops_targets, replace_targets
        context = get_context()
        conn = context["conn"]
        cfg = context["cfg"]
        try:
            items = replace_targets(conn, request, cfg=cfg)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        cfg.ops_targets = load_ops_targets(conn)
        return {"items": items, "message": "主动运营目标已保存；后续调度将读取新目标"}

    @router.get("/api/presets")
    def list_presets():
        from src.core.industry_presets_repository import list_presets as read_presets
        return {"items": read_presets(get_context()["conn"])}

    @router.put("/api/presets/{key}")
    def save_custom_preset(key: str, request: dict):
        from src.core.industry_presets_repository import upsert_custom_preset
        context = get_context()
        try:
            return upsert_custom_preset(
                context["conn"], {**request, "key": key}, cfg=context["cfg"],
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/api/presets/{key}")
    def delete_custom_preset(key: str):
        from src.core.industry_presets_repository import delete_custom_preset
        context = get_context()
        try:
            deleted = delete_custom_preset(context["conn"], key, cfg=context["cfg"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "自定义行业模板不存在")
        return {"key": key, "deleted": True}

    @router.post("/api/presets/{key}/restore")
    def restore_custom_preset(key: str):
        from src.core.industry_presets_repository import restore_custom_preset
        context = get_context()
        try:
            item = restore_custom_preset(context["conn"], key, cfg=context["cfg"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if item is None:
            raise HTTPException(404, "行业模板不存在")
        return item

    @router.get("/api/presets/{key}/versions")
    def get_preset_versions(key: str):
        from src.core.industry_presets_repository import get_preset, list_preset_versions
        conn = get_context()["conn"]
        if get_preset(conn, key) is None:
            raise HTTPException(404, "行业模板不存在")
        return {"items": list_preset_versions(conn, key)}

    @router.post("/api/presets/{key}/apply")
    def apply_preset(key: str):
        from src.core.industry_presets_repository import get_preset
        context = get_context()
        conn = context["conn"]
        cfg = context["cfg"]
        preset = get_preset(conn, key)
        if preset is None:
            raise HTTPException(404, "行业模板不存在")
        if not preset.get("is_active", True):
            raise HTTPException(409, "该行业模板已归档，请先恢复后再应用")
        updates = {
            "ultimate_goal.positioning": preset["positioning"],
            "ultimate_goal.persona_keywords": list(preset["persona_keywords"]),
            "ultimate_goal.forbidden_directions": list(preset["forbidden_directions"]),
            "ultimate_goal.redline_keywords": sorted(
                set(cfg.ultimate_goal.redline_keywords) | set(preset["extra_redlines"])
            ),
        }
        try:
            patch_config(cfg, conn, updates)
        except (config_not_allowed_error, config_patch_error) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "key": key,
            "name": preset["name"],
            "positioning": preset["positioning"],
            "persona_keywords": preset["persona_keywords"],
            "dimensions": preset["dimensions"],
            "platform_notes": preset["platform_notes"],
            "extra_redlines": preset["extra_redlines"],
            "message": "画像与红线已更新；建议维度可在「内容生产」里填入母计划",
        }

    return router
