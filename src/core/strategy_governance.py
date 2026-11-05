"""Canonical strategic inputs shared by diagnosis and plan generation.

Production-derived history is intentionally excluded from the plan-generation
freshness identity; strategic business/resource evidence and North Star versions
remain version-bound.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


_VOLATILE_RESOURCE_CATEGORIES = {"historical_output", "historical_performance"}


def collect_strategy_governance(conn) -> dict[str, Any]:
    from src.core.north_star_repository import get_current
    from src.core.resource_baseline_repository import build_resource_baseline

    north_star = get_current(conn)
    baseline = build_resource_baseline(conn)
    resources = [
        item for item in baseline.get("resources", [])
        if item.get("category") not in _VOLATILE_RESOURCE_CATEGORIES
    ]
    gaps = [
        item for item in baseline.get("gaps", [])
        if item.get("category") not in _VOLATILE_RESOURCE_CATEGORIES
    ]
    summary = {
        "total": len(resources),
        "confirmed": sum(item.get("status") == "confirmed" for item in resources),
        "pending": sum(item.get("status") == "pending" for item in resources),
        "restricted": sum(item.get("status") == "restricted" for item in resources),
        "rejected": sum(item.get("status") == "rejected" for item in resources),
        "missing": sum(item.get("status") == "missing" for item in resources),
        "unknown": sum(item.get("status") == "unknown" for item in resources),
        "gaps": len(gaps),
    }
    return {
        "north_star": ({
            "version_id": int(north_star["id"]),
            "version_no": int(north_star["version_no"]),
            "snapshot": north_star["snapshot"],
        } if north_star else None),
        "resource_baseline": {
            "resources": resources,
            "gaps": gaps,
            "summary": summary,
        },
    }


def strategy_governance_fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def strategy_governance_from_diagnosis_snapshot(source_snapshot: Any) -> dict[str, Any] | None:
    if not isinstance(source_snapshot, dict):
        return None
    value = source_snapshot.get("strategy_governance")
    return value if isinstance(value, dict) else None


def load_master_strategy_context(conn, master_row) -> dict[str, Any]:
    """Read the exact North Star and resource-baseline snapshots bound to a master version."""
    north_star = None
    north_star_id = master_row["north_star_version_id"] if "north_star_version_id" in master_row.keys() else None
    north_star_json = master_row["north_star_snapshot_json"] if "north_star_snapshot_json" in master_row.keys() else None
    if north_star_id is not None and north_star_json:
        try:
            north_star = {"version_id": int(north_star_id), "snapshot": json.loads(north_star_json)}
        except (TypeError, ValueError):
            north_star = {"version_id": int(north_star_id), "snapshot": None, "invalid": True}
    from src.core.resource_baseline_repository import get_plan_resource_baseline
    baseline = get_plan_resource_baseline(conn, int(master_row["id"]))
    return {
        "north_star": north_star,
        "resource_baseline": baseline,
        "resource_baseline_available": baseline is not None,
    }
