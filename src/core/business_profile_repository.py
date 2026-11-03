"""Persistence helpers for tenant-scoped business profiles and catalog records."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

ENTITY_TYPES = {"product", "service", "audience", "market"}
EVIDENCE_STATES = {"user_confirmed", "external_source", "ai_inferred", "missing"}
PROFILE_FIELDS = {
    "business_name", "introduction", "industry", "business_model",
    "business_stage", "operating_goal",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def validate_evidence(evidence: Any, fields: set[str]) -> dict:
    if evidence is None:
        return {}
    if not isinstance(evidence, dict):
        raise ValueError("字段来源信息必须是对象")
    result = {}
    for field, item in evidence.items():
        if field not in fields or not isinstance(item, dict):
            raise ValueError("字段来源信息包含未知字段或无效结构")
        status = item.get("status", "user_confirmed")
        if status not in EVIDENCE_STATES:
            raise ValueError("来源状态必须是 user_confirmed、external_source、ai_inferred 或 missing")
        source = item.get("source")
        if source is not None and (not isinstance(source, str) or len(source) > 1000):
            raise ValueError("来源说明不能超过 1000 个字符")
        if status == "external_source" and not source:
            raise ValueError("外部来源信息必须提供来源说明")
        result[field] = {
            "status": status,
            "source": source,
            "confirmed_at": item.get("confirmed_at"),
        }
    return result


def get_profile(conn) -> dict:
    row = conn.execute("SELECT * FROM business_profiles WHERE id=1").fetchone()
    if row is None:
        return {
            "business_name": "", "introduction": "", "industry": "",
            "business_model": "", "business_stage": "", "operating_goal": "",
            "field_evidence": {}, "version_no": 0, "updated_at": None,
        }
    item = dict(row)
    item["field_evidence"] = _loads(item.pop("field_evidence_json"), {})
    return item


def save_profile(conn, payload: dict, *, actor: str = "user") -> dict:
    unknown = set(payload) - PROFILE_FIELDS - {"field_evidence", "change_note"}
    if unknown:
        raise ValueError("包含不支持的业务档案字段")
    current = get_profile(conn)
    values = {key: str(payload.get(key, current.get(key, "")) or "").strip() for key in PROFILE_FIELDS}
    for key, value in values.items():
        if len(value) > (4000 if key in {"introduction", "operating_goal"} else 500):
            raise ValueError(f"{key} 内容过长")
    evidence = validate_evidence(payload.get("field_evidence", current["field_evidence"]), PROFILE_FIELDS)
    # User edits are explicit confirmation unless the caller supplies field-level provenance.
    for key in PROFILE_FIELDS:
        if key in payload and key not in evidence:
            evidence[key] = {"status": "user_confirmed", "source": None, "confirmed_at": _now()}
    changed = any(values[k] != current.get(k, "") for k in PROFILE_FIELDS) or evidence != current["field_evidence"]
    if not changed and current["version_no"]:
        return current
    version = int(current["version_no"]) + 1
    now = _now()
    conn.execute(
        """INSERT INTO business_profiles(id,business_name,introduction,industry,business_model,
           business_stage,operating_goal,field_evidence_json,version_no,updated_at)
           VALUES(1,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET business_name=excluded.business_name,
           introduction=excluded.introduction,industry=excluded.industry,
           business_model=excluded.business_model,business_stage=excluded.business_stage,
           operating_goal=excluded.operating_goal,field_evidence_json=excluded.field_evidence_json,
           version_no=excluded.version_no,updated_at=excluded.updated_at""",
        (values["business_name"], values["introduction"], values["industry"],
         values["business_model"], values["business_stage"], values["operating_goal"],
         _json(evidence), version, now),
    )
    snapshot = get_snapshot(conn)
    conn.execute(
        "INSERT INTO business_profile_versions(version_no,snapshot_json,change_note,created_at,created_by) VALUES(?,?,?,?,?)",
        (version, _json(snapshot), str(payload.get("change_note") or "")[:1000], now, actor[:100]),
    )
    return get_profile(conn)


def get_snapshot(conn) -> dict:
    profile = get_profile(conn)
    rows = conn.execute(
        "SELECT * FROM business_catalog_items WHERE is_active=1 ORDER BY entity_type,name,id"
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = _loads(item.pop("details_json"), {})
        item["field_evidence"] = _loads(item.pop("field_evidence_json"), {})
        items.append(item)
    return {"profile": profile, "items": items}


def list_items(conn, entity_type: str | None = None) -> list[dict]:
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        raise ValueError("不支持的业务资料类型")
    sql = "SELECT * FROM business_catalog_items WHERE is_active=1"
    params: tuple = ()
    if entity_type:
        sql += " AND entity_type=?"
        params = (entity_type,)
    sql += " ORDER BY entity_type,name,id"
    result = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        item["details"] = _loads(item.pop("details_json"), {})
        item["field_evidence"] = _loads(item.pop("field_evidence_json"), {})
        result.append(item)
    return result



def _record_catalog_version(conn, change_note: str) -> None:
    # Catalog changes advance the same aggregate revision as business profile changes.
    current = get_profile(conn)
    if current["version_no"] == 0:
        save_profile(conn, {})
        current = get_profile(conn)
    version = int(current["version_no"]) + 1
    now = _now()
    conn.execute(
        "UPDATE business_profiles SET version_no=?,updated_at=? WHERE id=1",
        (version, now),
    )
    conn.execute(
        "INSERT INTO business_profile_versions(version_no,snapshot_json,change_note,created_at,created_by) VALUES(?,?,?,?,?)",
        (version, _json(get_snapshot(conn)), change_note[:1000], now, "user"),
    )


def save_item(conn, entity_type: str, payload: dict, item_id: str | None = None) -> dict:
    if entity_type not in ENTITY_TYPES:
        raise ValueError("不支持的业务资料类型")
    if not isinstance(payload, dict):
        raise ValueError("业务资料必须是对象")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 160:
        raise ValueError("名称不能为空且不能超过 160 个字符")
    details = payload.get("details") or {}
    if not isinstance(details, dict):
        raise ValueError("details 必须是对象")
    if len(_json(details)) > 20000:
        raise ValueError("资料详情不能超过 20000 个字符")
    evidence = validate_evidence(payload.get("field_evidence"), set(details))
    for field in details:
        if field not in evidence:
            evidence[field] = {"status": "user_confirmed", "source": None, "confirmed_at": _now()}
    now = _now()
    if item_id:
        row = conn.execute("SELECT entity_type FROM business_catalog_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError("业务资料不存在")
        if row["entity_type"] != entity_type:
            raise ValueError("不能修改业务资料的类型")
        conn.execute(
            """UPDATE business_catalog_items SET name=?,details_json=?,field_evidence_json=?,updated_at=?
               WHERE id=?""", (name, _json(details), _json(evidence), now, item_id),
        )
    else:
        item_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO business_catalog_items(id,entity_type,name,details_json,field_evidence_json,
               created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
            (item_id, entity_type, name, _json(details), _json(evidence), now, now),
        )
    _record_catalog_version(conn, f"{entity_type}资料变更：{name}")
    return next(item for item in list_items(conn, entity_type) if item["id"] == item_id)


def archive_item(conn, item_id: str) -> bool:
    cursor = conn.execute(
        "UPDATE business_catalog_items SET is_active=0,updated_at=? WHERE id=? AND is_active=1",
        (_now(), item_id),
    )
    if cursor.rowcount == 1:
        _record_catalog_version(conn, "归档业务资料")
    return cursor.rowcount == 1


def list_versions(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT version_no,change_note,created_at,created_by FROM business_profile_versions ORDER BY version_no DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def plan_snapshot(conn, master_plan_version_id: int) -> dict | None:
    row = conn.execute(
        "SELECT business_profile_version_no,snapshot_json,captured_at FROM master_plan_business_snapshots WHERE master_plan_version_id=?",
        (master_plan_version_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["snapshot"] = _loads(result.pop("snapshot_json"), {})
    return result


def get_version(conn, version_no: int) -> dict | None:
    row = conn.execute(
        "SELECT version_no,snapshot_json,change_note,created_at,created_by FROM business_profile_versions WHERE version_no=?",
        (version_no,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["snapshot"] = _loads(result.pop("snapshot_json"), {})
    return result
