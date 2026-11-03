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

_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_SCHEMA_KEYS = {"type", "properties", "required", "items", "enum", "additionalProperties",
                "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}


def _schema_definition_errors(schema: Any, path: str = "$", depth: int = 0) -> list[dict]:
    errors: list[dict] = []
    if depth > 12 or not isinstance(schema, dict):
        return [{"field": path, "code": "schema_type", "message": "契约 Schema 必须是有限深度的 JSON 对象"}]
    for key in sorted(set(schema) - _SCHEMA_KEYS):
        errors.append({"field": f"{path}.{key}", "code": "unsupported_schema_keyword", "message": "不支持的 Schema 关键字"})
    kind = schema.get("type")
    if kind is not None and (not isinstance(kind, str) or kind not in _SCHEMA_TYPES):
        errors.append({"field": f"{path}.type", "code": "schema_type", "message": "不支持的类型"})
    props = schema.get("properties", {})
    if not isinstance(props, dict) or any(not isinstance(k, str) for k in props):
        errors.append({"field": f"{path}.properties", "code": "schema_type", "message": "properties 必须是对象"})
    else:
        for key, child in props.items():
            errors.extend(_schema_definition_errors(child, f"{path}.properties.{key}", depth + 1))
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        errors.append({"field": f"{path}.required", "code": "schema_type", "message": "required 必须是字符串数组"})
    elif isinstance(props, dict) and any(x not in props for x in required):
        errors.append({"field": f"{path}.required", "code": "unknown_required", "message": "required 字段必须在 properties 中定义"})
    if "items" in schema:
        errors.extend(_schema_definition_errors(schema["items"], f"{path}.items", depth + 1))
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        errors.append({"field": f"{path}.enum", "code": "schema_type", "message": "enum 必须是非空数组"})
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        errors.append({"field": f"{path}.additionalProperties", "code": "schema_type", "message": "additionalProperties 只支持布尔值"})
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (not isinstance(schema[key], int) or isinstance(schema[key], bool) or schema[key] < 0):
            errors.append({"field": f"{path}.{key}", "code": "schema_bound", "message": "长度边界必须是非负整数"})
    for key in ("minimum", "maximum"):
        if key in schema and (not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)):
            errors.append({"field": f"{path}.{key}", "code": "schema_bound", "message": "数值边界必须是数字"})
    if isinstance(schema.get("minLength"), int) and isinstance(schema.get("maxLength"), int) and schema["minLength"] > schema["maxLength"]:
        errors.append({"field": path, "code": "schema_bounds", "message": "最小长度不能大于最大长度"})
    if isinstance(schema.get("minItems"), int) and isinstance(schema.get("maxItems"), int) and schema["minItems"] > schema["maxItems"]:
        errors.append({"field": path, "code": "schema_bounds", "message": "最小数组长度不能大于最大值"})
    if isinstance(schema.get("minimum"), (int, float)) and isinstance(schema.get("maximum"), (int, float)) and schema["minimum"] > schema["maximum"]:
        errors.append({"field": path, "code": "schema_bounds", "message": "最小值不能大于最大值"})
    return errors


def validate_contract(schema: Any, value: Any) -> dict:
    """Validate JSON data against the deliberately small, non-executable schema subset."""
    definition_errors = _schema_definition_errors(schema)
    if definition_errors:
        return {"supported": True, "valid": False, "errors": definition_errors}
    errors: list[dict] = []

    def visit(rule: dict, item: Any, path: str, depth: int = 0) -> None:
        if depth > 16:
            errors.append({"path": path, "code": "max_depth"})
            return
        kind = rule.get("type")
        checks = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "null": lambda x: x is None,
        }
        if kind in checks and not checks[kind](item):
            errors.append({"path": path, "code": "type", "expected": kind})
            return
        if "enum" in rule and item not in rule["enum"]:
            errors.append({"path": path, "code": "enum"})
        if isinstance(item, dict):
            props = rule.get("properties", {})
            for required in rule.get("required", []):
                if required not in item:
                    errors.append({"path": f"{path}.{required}", "code": "required"})
            for key, child_value in item.items():
                if key in props:
                    visit(props[key], child_value, f"{path}.{key}", depth + 1)
                elif rule.get("additionalProperties") is False:
                    errors.append({"path": f"{path}.{key}", "code": "additional_property"})
        elif isinstance(item, list):
            if len(item) < rule.get("minItems", 0):
                errors.append({"path": path, "code": "min_items"})
            if len(item) > rule.get("maxItems", float("inf")):
                errors.append({"path": path, "code": "max_items"})
            if "items" in rule:
                for index, child_value in enumerate(item):
                    visit(rule["items"], child_value, f"{path}[{index}]", depth + 1)
        elif isinstance(item, str):
            if len(item) < rule.get("minLength", 0):
                errors.append({"path": path, "code": "min_length"})
            if len(item) > rule.get("maxLength", float("inf")):
                errors.append({"path": path, "code": "max_length"})
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            if item < rule.get("minimum", float("-inf")):
                errors.append({"path": path, "code": "minimum"})
            if item > rule.get("maximum", float("inf")):
                errors.append({"path": path, "code": "maximum"})

    visit(schema, value, "$")
    return {"supported": True, "valid": not errors, "errors": errors}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_manifest(manifest: dict) -> dict:
    errors: list[dict] = []
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": [{"field": "manifest", "code": "type", "message": "必须是 JSON 对象"}]}
    allowed = {"skill_id", "name", "version", "description", "category", "instructions", "permissions", "inputs", "outputs", "config"}
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
    category = manifest.get("category", "custom")
    if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}", category):
        errors.append({"field": "category", "code": "format", "message": "分类标识格式无效"})
    instructions = manifest.get("instructions", "")
    if not isinstance(instructions, str) or len(instructions) > 16000:
        errors.append({"field": "instructions", "code": "length", "message": "指令必须为字符串且最多 16000 字符"})
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list) or any(not isinstance(p, str) or p not in _ALLOWED_PERMISSIONS for p in permissions):
        errors.append({"field": "permissions", "code": "denied", "message": "权限包含未支持或高风险能力；默认拒绝"})
    for key in ("inputs", "outputs", "config"):
        if key in manifest and not isinstance(manifest[key], (dict, list)):
            errors.append({"field": key, "code": "type", "message": "必须为 JSON 对象或数组"})
        elif key in ("inputs", "outputs") and isinstance(manifest.get(key), dict) and "type" in manifest[key]:
            errors.extend(_schema_definition_errors(manifest[key], f"$.{key}"))
    try:
        encoded = _json(manifest).encode("utf-8")
        if len(encoded) > _MAX_MANIFEST_BYTES:
            errors.append({"field": "manifest", "code": "too_large", "message": "Manifest 超过 64 KiB"})
    except (TypeError, ValueError):
        errors.append({"field": "manifest", "code": "json", "message": "Manifest 不能序列化为 JSON"})
        encoded = b""
    return {"valid": not errors, "errors": errors, "content_sha256": hashlib.sha256(encoded).hexdigest()}


def _audit(conn: sqlite3.Connection, skill_id: str, action: str, details: dict | None = None,
           actor: str = "system") -> None:
    conn.execute("INSERT INTO tenant_skill_audit(skill_id,action,actor,details_json,created_at) VALUES(?,?,?,?,?)",
                 (skill_id, action, actor, _json(details or {}), _now()))


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
         manifest.get("category", "custom"), _json(manifest.get("inputs",{})), _json(manifest.get("outputs",{})),
         _json(manifest.get("permissions",[])), now, now, "user", payload, result["content_sha256"]))
    conn.execute("UPDATE tenant_skill_registry SET validation_json=? WHERE skill_id=?",
                 (_json(result), skill_id))
    conn.execute("INSERT INTO tenant_skill_versions(skill_id,version,manifest_json,content_sha256,created_at) VALUES(?,?,?,?,?)",
                 (skill_id, manifest["version"], payload, result["content_sha256"], now))
    _audit(conn, skill_id, "created", {"version": manifest["version"]}, actor)
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
    current_core = tuple(int(part) for part in current["version"].split("-", 1)[0].split("."))
    next_core = tuple(int(part) for part in manifest["version"].split("-", 1)[0].split("."))
    if next_core <= current_core:
        raise ValueError({"valid": False, "errors": [{"field":"version","code":"not_increased","message":"更新必须使用更高的主/次/修订版本号"}]})
    now, payload = _now(), _json(manifest)
    try:
        conn.execute("""UPDATE tenant_skill_registry SET name=?,version=?,description=?,category=?,inputs_json=?,outputs_json=?,
            permissions_json=?,manifest_json=?,content_sha256=?,validation_json=?,manifest_version=manifest_version+1,
            updated_at=? WHERE skill_id=?""",
            (manifest["name"].strip(),manifest["version"],manifest.get("description",""),manifest.get("category","custom"),
             _json(manifest.get("inputs",{})),_json(manifest.get("outputs",{})),_json(manifest.get("permissions",[])),
             payload,result["content_sha256"],_json(result),now,skill_id))
        conn.execute("INSERT INTO tenant_skill_versions(skill_id,version,manifest_json,content_sha256,created_at) VALUES(?,?,?,?,?)",
                     (skill_id,manifest["version"],payload,result["content_sha256"],now))
    except sqlite3.IntegrityError as exc:
        raise ValueError("该版本已存在") from exc
    _audit(conn, skill_id, "updated", {"version": manifest["version"]}, actor)
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
    _audit(conn, skill_id, "enabled" if enabled else "disabled", actor=actor)
    return get_skill(conn, skill_id)


def delete_skill(conn: sqlite3.Connection, skill_id: str, actor: str = "system") -> bool:
    if get_skill(conn, skill_id) is None:
        return False
    refs = conn.execute("SELECT reference_type,reference_id FROM tenant_skill_references WHERE skill_id=? LIMIT 1",
                        (skill_id,)).fetchone()
    if refs:
        raise RuntimeError("Skill 正在被引用，不能删除")
    _audit(conn, skill_id, "deleted", actor=actor)
    conn.execute("DELETE FROM tenant_skill_versions WHERE skill_id=?", (skill_id,))
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
