"""Tenant-scoped Skill manifest registry. Manifests are metadata, never executable code."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")
_ALLOWED_PERMISSIONS = {"read:own_inputs"}
_MAX_MANIFEST_BYTES = 64 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_manifest(manifest: dict) -> dict:
    errors: list[dict] = []
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": [{"field": "manifest", "code": "type", "message": "必须是 JSON 对象"}]}
    allowed = {"skill_id", "name", "version", "description", "instructions", "permissions", "inputs", "outputs", "config"}
    for key in sorted(set(manifest) - allowed):
        errors.append({"field": key, "code": "unknown_field", "message": "不支持的字段"})
    skill_id = manifest.get("skill_id")
    if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
        errors.append({"field": "skill_id", "code": "format", "message": "必须为 2-64 位小写字母、数字、下划线或连字符，且以字母开头"})
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        errors.append({"field": "name", "code": "required", "message": "名称必填且最多 120 字符"})
    version = manifest.get("version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        errors.append({"field": "version", "code": "semver", "message": "版本必须符合语义化版本格式"})
    description = manifest.get("description", "")
    if not isinstance(description, str) or len(description) > 2000:
        errors.append({"field": "description", "code": "length", "message": "描述必须为字符串且最多 2000 字符"})
    instructions = manifest.get("instructions", "")
    if not isinstance(instructions, str) or len(instructions) > 16000:
        errors.append({"field": "instructions", "code": "length", "message": "指令必须为字符串且最多 16000 字符"})
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list) or any(p not in _ALLOWED_PERMISSIONS for p in permissions):
        errors.append({"field": "permissions", "code": "denied", "message": "权限包含未支持或高风险能力；默认拒绝"})
    for key in ("inputs", "outputs", "config"):
        if key in manifest and not isinstance(manifest[key], (dict, list)):
            errors.append({"field": key, "code": "type", "message": "必须为 JSON 对象或数组"})
    try:
        encoded = _json(manifest).encode("utf-8")
        if len(encoded) > _MAX_MANIFEST_BYTES:
            errors.append({"field": "manifest", "code": "too_large", "message": "Manifest 超过 64 KiB"})
    except (TypeError, ValueError):
        errors.append({"field": "manifest", "code": "json", "message": "Manifest 不能序列化为 JSON"})
        encoded = b""
    return {"valid": not errors, "errors": errors, "content_sha256": hashlib.sha256(encoded).hexdigest()}


def _audit(conn: sqlite3.Connection, skill_id: str, action: str, details: dict | None = None) -> None:
    conn.execute("INSERT INTO tenant_skill_audit(skill_id,action,details_json,created_at) VALUES(?,?,?,?)",
                 (skill_id, action, _json(details or {}), _now()))


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("manifest_json", "validation_json", "permissions_json", "inputs_json", "outputs_json"):
        if key in item:
            try:
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            except (TypeError, ValueError):
                item[key.removesuffix("_json")] = {}
    item["execution_enabled"] = False
    item["execution_state"] = "blocked_no_sandbox"
    return item


def list_skills(conn: sqlite3.Connection) -> list[dict]:
    return [_decode(row) for row in conn.execute(
        "SELECT * FROM tenant_skill_registry ORDER BY name COLLATE NOCASE, skill_id"
    ).fetchall()]


def get_skill(conn: sqlite3.Connection, skill_id: str) -> dict | None:
    return _decode(conn.execute("SELECT * FROM tenant_skill_registry WHERE skill_id=?", (skill_id,)).fetchone())


def create_skill(conn: sqlite3.Connection, manifest: dict, actor: str = "system") -> dict:
    result = validate_manifest(manifest)
    if not result["valid"]:
        raise ValueError(result)
    skill_id = manifest["skill_id"]
    if get_skill(conn, skill_id):
        raise FileExistsError("Skill 标识已存在")
    now, payload = _now(), _json(manifest)
    conn.execute("""INSERT INTO tenant_skill_registry
        (skill_id,name,version,description,category,inputs_json,outputs_json,permissions_json,
         enabled,manifest_version,created_at,updated_at,source,manifest_json,content_sha256,validation_json,execution_state)
        VALUES(?,?,?,?,?,?,?,?,0,1,?,?,?,?,?,'{}','blocked_no_sandbox')""",
        (skill_id, manifest["name"].strip(), manifest["version"], manifest.get("description",""),
         "custom", _json(manifest.get("inputs",{})), _json(manifest.get("outputs",{})),
         _json(manifest.get("permissions",[])), now, now, "user", payload, result["content_sha256"]))
    conn.execute("INSERT INTO tenant_skill_versions(skill_id,version,manifest_json,content_sha256,created_at) VALUES(?,?,?,?,?)",
                 (skill_id, manifest["version"], payload, result["content_sha256"], now))
    _audit(conn, skill_id, "created", {"version": manifest["version"], "actor": actor})
    return get_skill(conn, skill_id)


def update_skill(conn: sqlite3.Connection, skill_id: str, manifest: dict, actor: str = "system") -> dict:
    result = validate_manifest(manifest)
    if not result["valid"]:
        raise ValueError(result)
    if manifest.get("skill_id") != skill_id:
        raise ValueError({"valid": False, "errors": [{"field":"skill_id","code":"immutable","message":"Skill 标识不可修改"}]})
    current = get_skill(conn, skill_id)
    if current is None:
        return None
    if current["version"] == manifest["version"]:
        raise ValueError({"valid": False, "errors": [{"field":"version","code":"immutable","message":"更新内容必须提升版本号"}]})
    now, payload = _now(), _json(manifest)
    try:
        conn.execute("""UPDATE tenant_skill_registry SET name=?,version=?,description=?,inputs_json=?,outputs_json=?,
            permissions_json=?,manifest_json=?,content_sha256=?,validation_json='{}',manifest_version=manifest_version+1,
            updated_at=? WHERE skill_id=?""",
            (manifest["name"].strip(),manifest["version"],manifest.get("description",""),_json(manifest.get("inputs",{})),
             _json(manifest.get("outputs",{})),_json(manifest.get("permissions",[])),payload,result["content_sha256"],now,skill_id))
        conn.execute("INSERT INTO tenant_skill_versions(skill_id,version,manifest_json,content_sha256,created_at) VALUES(?,?,?,?,?)",
                     (skill_id,manifest["version"],payload,result["content_sha256"],now))
    except sqlite3.IntegrityError as exc:
        raise ValueError("该版本已存在") from exc
    _audit(conn, skill_id, "updated", {"version": manifest["version"], "actor": actor})
    return get_skill(conn, skill_id)


def set_enabled(conn: sqlite3.Connection, skill_id: str, enabled: bool, actor: str = "system") -> dict | None:
    item = get_skill(conn, skill_id)
    if item is None:
        return None
    if enabled:
        result = validate_manifest(item["manifest"])
        if not result["valid"]:
            raise ValueError(result)
    conn.execute("UPDATE tenant_skill_registry SET enabled=?,updated_at=? WHERE skill_id=?",
                 (int(enabled),_now(),skill_id))
    _audit(conn, skill_id, "enabled" if enabled else "disabled", {"actor": actor})
    return get_skill(conn, skill_id)


def delete_skill(conn: sqlite3.Connection, skill_id: str, actor: str = "system") -> bool:
    if get_skill(conn, skill_id) is None:
        return False
    refs = conn.execute("SELECT reference_type,reference_id FROM tenant_skill_references WHERE skill_id=? LIMIT 1",
                        (skill_id,)).fetchone()
    if refs:
        raise RuntimeError("Skill 正在被引用，不能删除")
    _audit(conn, skill_id, "deleted", {"actor": actor})
    conn.execute("DELETE FROM tenant_skill_registry WHERE skill_id=?", (skill_id,))
    return True


def list_versions(conn: sqlite3.Connection, skill_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT version,content_sha256,created_at FROM tenant_skill_versions WHERE skill_id=? ORDER BY id DESC",
        (skill_id,)).fetchall()]


def list_audit(conn: sqlite3.Connection, skill_id: str, limit: int = 100) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT action,actor,details_json,created_at FROM tenant_skill_audit WHERE skill_id=? ORDER BY id DESC LIMIT ?",
        (skill_id,limit)).fetchall()]
