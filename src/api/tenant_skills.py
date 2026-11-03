"""Tenant-scoped Skill registry APIs. Custom Skill execution is intentionally disabled."""
from __future__ import annotations

from typing import Callable
from fastapi import APIRouter, HTTPException, Query

from src.core import skill_execution, tenant_skills


def build_tenant_skills_router(get_context: Callable[[], dict]) -> APIRouter:
    router = APIRouter(prefix="/api/skill-registry")

    @router.get("/skills")
    def list_skills():
        return {"items": tenant_skills.list_skills(get_context()["conn"]), "execution_enabled": False}

    @router.post("/skills", status_code=201)
    def create_skill(request: dict):
        try:
            return tenant_skills.create_skill(get_context()["conn"], request)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=exc.args[0]) from exc

    @router.get("/skills/{skill_id}")
    def get_skill(skill_id: str):
        item = tenant_skills.get_skill(get_context()["conn"], skill_id)
        if item is None:
            raise HTTPException(404, "Skill 不存在")
        return item

    @router.put("/skills/{skill_id}")
    def update_skill(skill_id: str, request: dict):
        try:
            item = tenant_skills.update_skill(get_context()["conn"], skill_id, request)
        except ValueError as exc:
            raise HTTPException(422, detail=exc.args[0]) from exc
        if item is None:
            raise HTTPException(404, "Skill 不存在")
        return item

    @router.delete("/skills/{skill_id}")
    def delete_skill(skill_id: str):
        try:
            deleted = tenant_skills.delete_skill(get_context()["conn"], skill_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "Skill 不存在")
        return {"skill_id": skill_id, "deleted": True}

    @router.post("/skills/{skill_id}/enable")
    def enable_skill(skill_id: str):
        try:
            item = tenant_skills.set_enabled(get_context()["conn"], skill_id, True)
        except ValueError as exc:
            raise HTTPException(422, detail=exc.args[0]) from exc
        if item is None:
            raise HTTPException(404, "Skill 不存在")
        return item

    @router.post("/skills/{skill_id}/disable")
    def disable_skill(skill_id: str):
        item = tenant_skills.set_enabled(get_context()["conn"], skill_id, False)
        if item is None:
            raise HTTPException(404, "Skill 不存在")
        return item

    @router.post("/skills/validate")
    def validate_skill(request: dict):
        return tenant_skills.validate_manifest(request)

    @router.get("/skills/{skill_id}/preview")
    def preview_skill(skill_id: str):
        item = tenant_skills.get_skill(get_context()["conn"], skill_id)
        if item is None:
            raise HTTPException(404, "Skill 不存在")
        return {"skill_id": skill_id, "version": item["version"], "manifest": item["manifest"],
                "validation": tenant_skills.validate_manifest(item["manifest"]), "execution_enabled": False}

    @router.get("/skills/{skill_id}/versions")
    def versions(skill_id: str):
        conn = get_context()["conn"]
        if tenant_skills.get_skill(conn, skill_id) is None:
            raise HTTPException(404, "Skill 不存在")
        return {"items": tenant_skills.list_versions(conn, skill_id)}

    @router.get("/skills/{skill_id}/audit")
    def audit(skill_id: str, limit: int = Query(100, ge=1, le=500)):
        conn = get_context()["conn"]
        if tenant_skills.get_skill(conn, skill_id) is None:
            raise HTTPException(404, "Skill 不存在")
        return {"items": tenant_skills.list_audit(conn, skill_id, limit)}

    @router.get("/skills/{skill_id}/executions")
    def executions(skill_id: str, limit: int = Query(100, ge=1, le=500)):
        conn = get_context()["conn"]
        if tenant_skills.get_skill(conn, skill_id) is None:
            raise HTTPException(404, "Skill 不存在")
        return {"items": skill_execution.list_executions(conn, skill_id, limit)}

    @router.post("/skills/{skill_id}/execute")
    def execute_skill(skill_id: str, request: dict):
        conn = get_context()["conn"]
        skill = tenant_skills.get_skill(conn, skill_id)
        if skill is None:
            raise HTTPException(404, "Skill 不存在")
        result = skill_execution.request_execution(conn, skill, request)
        raise HTTPException(409, detail={
            "code": "execution_blocked",
            "message": "自定义 Skill 执行器尚未通过独立隔离安全验收；请求已拒绝并记录审计",
            **result,
        })

    return router
