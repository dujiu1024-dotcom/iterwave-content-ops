"""Hot-topic source configuration, diagnostics and history routes."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)


def build_hot_sources_router(
    get_context: Callable[[], dict],
    patch_config: Callable,
    config_not_allowed_error: type[Exception],
    config_patch_error: type[Exception],
    collector_factory: Callable,
    redact: Callable[[str], str],
    redact_remote_url: Callable[[str], str],
    storage_now: Callable,
    *,
    manages_own_transaction: Callable | None = None,
    route_class: type[APIRoute] = APIRoute,
) -> APIRouter:
    """Build tenant-scoped hot-source APIs with redacted public values."""
    router = APIRouter(route_class=route_class)
    if manages_own_transaction is None:
        manages_own_transaction = lambda endpoint: endpoint

    def public_sources(items: list[dict]) -> list[dict]:
        # URLs may contain signatures or access tokens; expose only redacted values.
        public = []
        for item in items or []:
            row = dict(item)
            raw_url = str(row.get("url") or "")
            row["url"] = redact_remote_url(raw_url) if raw_url else ""
            raw_name = str(row.get("name") or "")
            row["name"] = redact(raw_name) if raw_name else raw_name
            public.append(row)
        return public

    def restore_masked_urls(items: list[dict], current: list[dict]) -> list[dict]:
        # A list round-trip sends masked URLs back. Restore the stored URL only when
        # the source identity matches; new or changed sources keep their submitted URL.
        restored = []
        for incoming in items or []:
            row = dict(incoming)
            for old in current or []:
                # Include the display name (accepting its public redacted form) so
                # same-platform sources with colliding masked URLs cannot cross-restore.
                old_name = str(old.get("name") or "")
                incoming_name = str(row.get("name") or "")
                same_name = old_name == incoming_name or redact(old_name) == incoming_name
                same_key = (
                    same_name
                    and str(old.get("type") or "aggregate") == str(row.get("type") or "aggregate")
                    and str(old.get("platform") or "") == str(row.get("platform") or "")
                    and tuple(old.get("platforms") or []) == tuple(row.get("platforms") or [])
                )
                if same_key and str(row.get("url") or "") == redact_remote_url(str(old.get("url") or "")):
                    row["url"] = old.get("url") or ""
                    break
            restored.append(row)
        return restored

    def source_history_key(source: dict) -> str:
        # Persist only an irreversible fingerprint; source URLs may contain credentials.
        identity = {
            "name": str(source.get("name") or ""),
            "type": str(source.get("type") or "aggregate"),
            "platform": str(source.get("platform") or ""),
            "platforms": sorted(str(value) for value in (source.get("platforms") or [])),
            "url": str(source.get("url") or ""),
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def latest_source_test(conn, source: dict):
        row = conn.execute(
            "SELECT status, item_count, sample_titles_json, message, tested_at "
            "FROM hot_source_test_history WHERE source_key=? ORDER BY id DESC LIMIT 1",
            (source_history_key(source),),
        ).fetchone()
        if row is None:
            return None
        try:
            samples = json.loads(row["sample_titles_json"] or "[]")
        except (TypeError, ValueError):
            samples = []
        return {
            "status": row["status"],
            "item_count": int(row["item_count"] or 0),
            "sample_titles": samples if isinstance(samples, list) else [],
            "message": row["message"],
            "tested_at": row["tested_at"],
        }

    @router.get("/api/hot_topics/sources")
    def hot_sources_list():
        context = get_context()
        cfg_sources = context["cfg"].hot_topics.sources or []
        items = public_sources(cfg_sources)
        for index, item in enumerate(items):
            item["last_test"] = latest_source_test(context["conn"], cfg_sources[index])
        return {"items": items}

    @router.get("/api/hot_topics/sources/history")
    def hot_sources_test_history(limit: int = Query(100, ge=1, le=500)):
        rows = get_context()["conn"].execute(
            "SELECT id, source_key, source_label, source_type, status, item_count, "
            "sample_titles_json, message, tested_at FROM hot_source_test_history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["sample_titles"] = json.loads(item.pop("sample_titles_json") or "[]")
            except (TypeError, ValueError):
                item["sample_titles"] = []
                item.pop("sample_titles_json", None)
            items.append(item)
        return {"items": items, "total": len(items)}

    @router.put("/api/hot_topics/sources")
    def hot_sources_update(request: dict):
        context = get_context()
        cfg = context["cfg"]
        items = (request or {}).get("items")
        if not isinstance(items, list):
            raise HTTPException(422, "热点源必须是数组")
        items = restore_masked_urls(items, cfg.hot_topics.sources)
        try:
            patch_config(cfg, context["conn"], {"hot_topics.sources": items})
        except (config_not_allowed_error, config_patch_error) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"items": public_sources(cfg.hot_topics.sources)}

    @router.post("/api/hot_topics/sources/{source_index}/test")
    @manages_own_transaction
    def test_hot_source(source_index: int):
        """Fetch one RSS/JSON source for diagnostics without importing rows into hot_topics."""
        context = get_context()
        cfg = context["cfg"]
        sources = cfg.hot_topics.sources or []
        if source_index < 0 or source_index >= len(sources):
            raise HTTPException(404, "热点来源不存在")
        source = sources[source_index]
        source_type = str(source.get("type") or "aggregate")
        if source_type not in ("rss", "json"):
            raise HTTPException(400, "单来源试抓取仅支持 RSS / JSON 来源")
        label = str(source.get("platform") or source.get("name") or "行业源")
        collector = collector_factory(cfg, context["conn"])
        try:
            rows = collector._fetch_custom(source, label)
        except Exception as exc:  # noqa: BLE001 — diagnostic fetch must not abort other sources
            safe_error = redact(str(exc))
            logger.warning("热点来源试抓取失败 %s: %s", redact(label), safe_error)
            rows = None
            fetch_error = safe_error
        else:
            fetch_error = None
        if fetch_error:
            result = {
                "ok": False, "status": "failed", "source": redact(label),
                "item_count": 0, "sample_titles": [], "message": fetch_error,
            }
        elif not rows:
            result = {
                "ok": False, "status": "empty", "source": redact(label),
                "item_count": 0, "sample_titles": [], "message": "来源可访问，但未解析到有效标题",
            }
        else:
            result = {
                "ok": True, "status": "success", "source": redact(label),
                "item_count": len(rows),
                "sample_titles": [redact(str(row.get("title") or ""))[:300] for row in rows[:3]],
                "message": "试抓取成功，未写入热点库",
            }
        conn = context["conn"]
        conn.execute(
            "INSERT INTO hot_source_test_history "
            "(source_key, source_label, source_type, status, item_count, sample_titles_json, message, tested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_history_key(source), redact(label)[:160], source_type,
                result["status"], result["item_count"],
                json.dumps(result["sample_titles"], ensure_ascii=False),
                redact(result["message"])[:1000],
                storage_now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return result

    return router
