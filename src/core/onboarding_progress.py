"""正式运营设置向导进度（按账号组持久化到 app_settings.onboarding_progress）。

进度属于账号组的业务状态：换浏览器、换设备、热切换账号组后都应一致，
因此存后端数据库而非浏览器 localStorage。每个账号组有独立数据库，
所以同一个 key 天然按账号组隔离。
"""

from __future__ import annotations

import json
import re
from src.core.timezone import business_now
from typing import Optional

_KEY = "onboarding_progress"
_STEP_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_STEP_INDEX = 50


def get_onboarding_progress(conn) -> dict:
    """返回 {step_key, step_index, updated_at}；从未保存过或表缺失时字段为 None。"""
    empty = {"step_key": None, "step_index": None, "updated_at": None}
    try:
        row = conn.execute(
            "SELECT value, updated_at FROM app_settings WHERE key = ?", (_KEY,)
        ).fetchone()
    except Exception:  # noqa: BLE001 — app_settings 未迁移的老库按未保存处理
        return empty
    if row is None:
        return empty
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return empty
    step_key = data.get("step_key") if isinstance(data, dict) else None
    step_index = data.get("step_index") if isinstance(data, dict) else None
    if not isinstance(step_key, str) or not _STEP_KEY_RE.fullmatch(step_key):
        step_key = None
    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index <= MAX_STEP_INDEX:
        step_index = None
    return {"step_key": step_key, "step_index": step_index, "updated_at": row[1]}


def validate_onboarding_progress(req: Optional[dict]) -> tuple[str, int]:
    """校验请求体，返回 (step_key, step_index)；非法时抛 ValueError（中文提示）。"""
    req = req or {}
    step_key = req.get("step_key")
    step_index = req.get("step_index")
    if not isinstance(step_key, str) or not _STEP_KEY_RE.fullmatch(step_key):
        raise ValueError("step_key 必须是小写字母开头的步骤标识")
    if not isinstance(step_index, int) or isinstance(step_index, bool) or not 0 <= step_index <= MAX_STEP_INDEX:
        raise ValueError(f"step_index 必须是 0~{MAX_STEP_INDEX} 之间的整数")
    return step_key, step_index


def set_onboarding_progress(conn, step_key: str, step_index: int, by: str = "web-ui", cfg=None) -> dict:
    now = business_now(cfg).strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (_KEY, json.dumps({"step_key": step_key, "step_index": step_index}, ensure_ascii=False), now, by),
        )
    return {"step_key": step_key, "step_index": step_index, "updated_at": now}
