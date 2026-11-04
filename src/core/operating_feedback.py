"""Publication verification, independent reviews and operating-feedback facts."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


METRIC_UNITS = {
    "views": "count", "likes": "count", "collects": "count",
    "comments": "count", "shares": "count", "new_followers": "count",
    "conversions": "count", "like_rate": "ratio", "collect_rate": "ratio",
    "interaction_rate": "ratio", "follower_efficiency": "ratio",
}
PLATFORMS = {"xhs", "douyin", "kuaishou", "wechat_channels"}


class OperatingFeedbackError(ValueError):
    """A publication, review or operating-feedback transition is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str | None, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _normalize_datetime(
    value: str, timezone_name: str = "UTC", label: str = "时间",
    *, require_timezone: bool = False,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise OperatingFeedbackError(f"{label}不能为空")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise OperatingFeedbackError(f"{label}格式无效") from exc
    if parsed.tzinfo is None:
        if require_timezone:
            raise OperatingFeedbackError(f"{label}必须包含时区")
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(str(timezone_name or "UTC")))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise OperatingFeedbackError(f"{label}时区无效") from exc
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _file_sha256(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_file():
        raise OperatingFeedbackError("发布图片文件不可读取，无法固化图片指纹")
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OperatingFeedbackError("发布图片文件读取失败，无法固化图片指纹") from exc
    return digest.hexdigest()


def _valid_https_url(value: str) -> bool:
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and parsed.password is None


def _platform_url_matches(platform: str, value: str) -> bool:
    from urllib.parse import urlsplit
    hosts = {
        "xhs": ("xiaohongshu.com", "xhslink.com"),
        "douyin": ("douyin.com", "iesdouyin.com"),
        "kuaishou": ("kuaishou.com", "gifshow.com"),
        "wechat_channels": ("weixin.qq.com", "weixin.com"),
    }
    try:
        hostname = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(hostname == domain or hostname.endswith("." + domain) for domain in hosts.get(platform, ()))


def create_publish_verification(
    conn, *, tenant_id: str, draft_id: str, platform_content_id: str,
    content_url: str, published_at: str, evidence_source: str = "user_confirmed",
    evidence_note: str = "", production_line_id: str | None = None,
    scheduled_review_at: str | None = None, max_attempts: int = 3,
    idempotency_key: str | None = None,
) -> dict:
    platform_content_id = str(platform_content_id or "").strip()
    content_url = str(content_url or "").strip()
    if not platform_content_id or len(platform_content_id) > 200:
        raise OperatingFeedbackError("平台内容 ID 必须为 1-200 个字符")
    if not _valid_https_url(content_url) or len(content_url) > 2048:
        raise OperatingFeedbackError("内容链接必须是有效的 HTTPS URL")
    if not published_at or len(str(published_at)) > 40:
        raise OperatingFeedbackError("真实发布时间无效")
    published_at = _normalize_datetime(published_at, "UTC", "真实发布时间", require_timezone=True)
    published_dt = datetime.fromisoformat(published_at)
    if published_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise OperatingFeedbackError("真实发布时间不能晚于当前时间")
    if evidence_source not in {"user_confirmed", "platform_readback", "manual_evidence"}:
        raise OperatingFeedbackError("发布证据来源无效")
    row = conn.execute(
        """SELECT d.id AS draft_id,d.platform,d.status,d.user_confirmed_at,d.published_at,
                  c.id AS content_id,c.production_task_id,c.title,c.body,c.tags,c.image_paths,
                  c.created_at AS content_created_at
           FROM drafts d JOIN contents c ON c.id=d.content_id
           WHERE d.id=?""", (draft_id,),
    ).fetchone()
    if row is None:
        raise OperatingFeedbackError("草稿不存在")
    if row["platform"] not in PLATFORMS:
        raise OperatingFeedbackError("草稿所属平台无效")
    if not _platform_url_matches(row["platform"], content_url):
        raise OperatingFeedbackError("内容链接域名与草稿所属平台不匹配")
    if row["user_confirmed_at"] is None or row["status"] not in {"user_published", "data_fetching", "published", "failed"}:
        raise OperatingFeedbackError("必须先由用户明确确认真实发布，不能用存稿状态代替发布核验")
    idem = str(idempotency_key or f"publish-verification:{draft_id}:{platform_content_id}").strip()
    if not idem or len(idem) > 200:
        raise OperatingFeedbackError("幂等键必须为 1-200 个字符")
    existing = conn.execute(
        "SELECT * FROM content_publish_verifications WHERE draft_id=?", (draft_id,)
    ).fetchone()
    if existing:
        same = (
            existing["platform_content_id"] == platform_content_id
            and existing["content_url"] == content_url
            and existing["published_at"] == published_at
            and existing["idempotency_key"] == idem
        )
        if not same:
            raise OperatingFeedbackError("该草稿已有发布核验记录，不能覆盖原始发布证据")
        task = conn.execute(
            "SELECT task_id FROM review_tasks WHERE verification_id=?",
            (existing["verification_id"],),
        ).fetchone()
        return _verification_dict(
            existing, already_verified=True,
            review_task_id=task["task_id"] if task else None,
        )

    verification_id = uuid.uuid4().hex
    now = utc_now()
    image_paths = _parse_json(row["image_paths"], [])
    if not isinstance(image_paths, list):
        image_paths = []
    image_files = []
    for raw_path in image_paths:
        path = Path(str(raw_path)).expanduser()
        try:
            resolved = path.resolve()
            if resolved.is_file():
                image_files.append({
                    "name": resolved.name,
                    "sha256": _file_sha256(resolved),
                    "size_bytes": resolved.stat().st_size,
                    "available": True,
                })
            else:
                image_files.append({"name": path.name, "sha256": None, "size_bytes": None, "available": False})
        except OSError:
            image_files.append({"name": path.name, "sha256": None, "size_bytes": None, "available": False})
    snapshot = {
        "content_id": row["content_id"], "draft_id": draft_id,
        "platform": row["platform"], "title": row["title"] or "",
        "body": row["body"] or "", "tags": _parse_json(row["tags"], []),
        "image_paths": [Path(str(path)).name for path in image_paths],
        "image_files": image_files,
        "production_task_id": row["production_task_id"],
        "content_created_at": row["content_created_at"],
    }
    snapshot_json = _json(snapshot)
    snapshot_sha256 = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT INTO content_publish_verifications
           (verification_id,tenant_id,draft_id,content_id,production_task_id,production_line_id,
            platform,platform_content_id,content_url,published_at,verified_at,
            content_snapshot_json,content_snapshot_sha256,evidence_source,evidence_note,idempotency_key,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (verification_id, tenant_id, draft_id, row["content_id"], row["production_task_id"],
         production_line_id, row["platform"], platform_content_id, content_url,
         published_at, now, snapshot_json, snapshot_sha256, evidence_source,
         str(evidence_note or "")[:1000], idem, now),
    )
    event_payload = {
        "event_type": "content.publish_verified.v1",
        "verification_id": verification_id,
        "tenant_id": tenant_id,
        "draft_id": draft_id,
        "content_id": row["content_id"],
        "production_task_id": row["production_task_id"],
        "production_line_id": production_line_id,
        "platform": row["platform"],
        "platform_content_id": platform_content_id,
        "content_url": content_url,
        "published_at": published_at,
        "verified_at": now,
        "content_snapshot": snapshot,
        "content_snapshot_sha256": snapshot_sha256,
        "idempotency_key": idem,
    }
    conn.execute(
        """INSERT INTO production_line_close_outbox
           (event_id,verification_id,event_type,payload_json,status,created_at,updated_at)
           VALUES(?,?, 'content.publish_verified.v1', ?, 'pending', ?, ?)""",
        (uuid.uuid4().hex, verification_id, _json(event_payload), now, now),
    )
    due_at = _normalize_datetime(scheduled_review_at, "UTC", "复盘计划时间") if scheduled_review_at else (
        published_dt + timedelta(hours=24)
    ).isoformat(timespec="seconds")
    task_id = uuid.uuid4().hex
    status = "pending" if due_at <= now else "scheduled"
    conn.execute(
        """INSERT INTO review_tasks
           (task_id,verification_id,tenant_id,draft_id,content_id,platform,status,
            scheduled_at,max_attempts,created_at,updated_at,idempotency_key)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, verification_id, tenant_id, draft_id, row["content_id"], row["platform"],
         status, due_at, max(1, min(int(max_attempts), 10)), now, now, f"review:{verification_id}"),
    )
    result = conn.execute(
        "SELECT * FROM content_publish_verifications WHERE verification_id=?", (verification_id,)
    ).fetchone()
    return _verification_dict(result, already_verified=False, review_task_id=task_id)


def _verification_dict(row, *, already_verified: bool, review_task_id: str | None = None) -> dict:
    item = dict(row)
    item["content_snapshot"] = _parse_json(item.pop("content_snapshot_json", None), {})
    if isinstance(item["content_snapshot"], dict) and isinstance(item["content_snapshot"].get("image_paths"), list):
        item["content_snapshot"]["image_paths"] = [
            Path(str(path)).name for path in item["content_snapshot"]["image_paths"]
        ]
    item["already_verified"] = already_verified
    if review_task_id:
        item["review_task_id"] = review_task_id
    return item


def list_close_events(conn, *, tenant_id: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT event_id,verification_id,event_type,payload_json,status,attempts,last_error,
                  created_at,updated_at
           FROM production_line_close_outbox
           WHERE json_extract(payload_json,'$.tenant_id')=?
             AND status IN ('pending','failed') ORDER BY created_at,event_id LIMIT ?""",
        (tenant_id, max(1, min(int(limit), 200))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = _parse_json(item.pop("payload_json"), {})
        result.append(item)
    return result


def claim_close_events(conn, *, tenant_id: str, worker_id: str, limit: int = 10,
                       lease_seconds: int = 120) -> list[dict]:
    worker = str(worker_id or "").strip()
    if not worker or len(worker) > 120:
        raise OperatingFeedbackError("worker_id 必须为 1-120 个字符")
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    lease_until = (now_dt + timedelta(seconds=max(15, min(int(lease_seconds), 900)))).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT event_id FROM production_line_close_outbox
           WHERE json_extract(payload_json,'$.tenant_id')=?
             AND (status IN ('pending','failed') OR
                  (status='processing' AND lease_expires_at IS NOT NULL AND lease_expires_at<?))
           ORDER BY created_at,event_id LIMIT ?""",
        (tenant_id, now, max(1, min(int(limit), 50))),
    ).fetchall()
    claimed = []
    for row in rows:
        changed = conn.execute(
            """UPDATE production_line_close_outbox
               SET status='processing',claimed_by=?,lease_expires_at=?,attempts=attempts+1,
                   last_error=NULL,updated_at=?
               WHERE event_id=? AND (status IN ('pending','failed') OR
                    (status='processing' AND lease_expires_at<?))""",
            (worker, lease_until, now, row["event_id"], now),
        )
        if changed.rowcount != 1:
            continue
        item = conn.execute(
            "SELECT event_id,verification_id,event_type,payload_json,status,attempts,lease_expires_at FROM production_line_close_outbox WHERE event_id=?",
            (row["event_id"],),
        ).fetchone()
        result = dict(item)
        result["payload"] = _parse_json(result.pop("payload_json"), {})
        claimed.append(result)
    return claimed


def acknowledge_close_event(
    conn, event_id: str, *, succeeded: bool, error: str = "", tenant_id: str | None = None,
) -> dict:
    row = conn.execute(
        "SELECT * FROM production_line_close_outbox WHERE event_id=?", (event_id,)
    ).fetchone()
    if row is None:
        raise OperatingFeedbackError("发布关闭事件不存在")
    payload = _parse_json(row["payload_json"], {})
    if tenant_id and payload.get("tenant_id") != tenant_id:
        raise OperatingFeedbackError("发布关闭事件不存在")
    if row["status"] == "delivered":
        return {"event_id": event_id, "status": "delivered", "already_acknowledged": True}
    now = utc_now()
    if succeeded:
        conn.execute(
            "UPDATE production_line_close_outbox SET status='delivered',claimed_by=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE event_id=?",
            (now, event_id),
        )
        conn.execute(
            "UPDATE content_publish_verifications SET closure_status='succeeded',closure_error=NULL WHERE verification_id=?",
            (row["verification_id"],),
        )
    else:
        message = str(error or "生产线关闭失败")[:1000]
        conn.execute(
            "UPDATE production_line_close_outbox SET status='failed',claimed_by=NULL,lease_expires_at=NULL,last_error=?,updated_at=? WHERE event_id=?",
            (message, now, event_id),
        )
        conn.execute(
            "UPDATE content_publish_verifications SET closure_status='failed',closure_error=? WHERE verification_id=?",
            (message, row["verification_id"]),
        )
    return {"event_id": event_id, "status": "delivered" if succeeded else "failed"}


def retry_close_event(conn, event_id: str, *, tenant_id: str | None = None) -> dict:
    row = conn.execute(
        "SELECT status FROM production_line_close_outbox WHERE event_id=?", (event_id,)
    ).fetchone()
    if row is None:
        raise OperatingFeedbackError("发布关闭事件不存在")
    if tenant_id:
        event = conn.execute(
            "SELECT payload_json FROM production_line_close_outbox WHERE event_id=?", (event_id,),
        ).fetchone()
        if not event or _parse_json(event["payload_json"], {}).get("tenant_id") != tenant_id:
            raise OperatingFeedbackError("发布关闭事件不存在")
    if row["status"] == "delivered":
        raise OperatingFeedbackError("已成功关闭的生产线事件不能重试")
    now = utc_now()
    conn.execute(
        "UPDATE production_line_close_outbox SET status='pending',claimed_by=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE event_id=?",
        (now, event_id),
    )
    conn.execute(
        "UPDATE content_publish_verifications SET closure_status='pending',closure_error=NULL WHERE verification_id=(SELECT verification_id FROM production_line_close_outbox WHERE event_id=?)",
        (event_id,),
    )
    return {"event_id": event_id, "status": "pending"}


def create_collection_run(
    conn, *, task_id: str, source: str, status: str, metrics: dict[str, Any] | None,
    window_start: str | None = None, window_end: str | None = None,
    timezone_name: str = "UTC", error_message: str = "",
    idempotency_key: str | None = None, measured_at: str | None = None,
) -> dict:
    task = conn.execute("SELECT * FROM review_tasks WHERE task_id=?", (task_id,)).fetchone()
    if task is None:
        raise OperatingFeedbackError("复盘任务不存在")
    if task["status"] == "cancelled":
        raise OperatingFeedbackError("已取消的复盘任务不能采集")
    if source not in {"platform_readback", "manual", "import"}:
        raise OperatingFeedbackError("采集来源无效")
    if status not in {"succeeded", "partial", "failed"}:
        raise OperatingFeedbackError("采集状态无效")
    metrics = metrics if isinstance(metrics, dict) else {}
    normalized: dict[str, float | int] = {}
    for key, raw in metrics.items():
        if key not in METRIC_UNITS:
            raise OperatingFeedbackError(f"不支持的指标：{key}")
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)) or raw < 0:
            raise OperatingFeedbackError(f"{key} 必须是非负数，缺失值请留空")
        if METRIC_UNITS[key] == "count" and int(raw) != raw:
            raise OperatingFeedbackError(f"{key} 必须是整数")
        normalized[key] = int(raw) if METRIC_UNITS[key] == "count" else float(raw)
    if status == "failed" and normalized:
        raise OperatingFeedbackError("采集失败不能携带指标值；失败不等于零")
    if status in {"succeeded", "partial"} and not normalized:
        raise OperatingFeedbackError("成功或部分成功必须至少包含一项真实采集值；真实零值请明确传入 0")
    try:
        business_zone = ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        raise OperatingFeedbackError("采集时区无效") from None
    business_now = datetime.now(business_zone)
    if window_start is None:
        window_start = (business_now - timedelta(days=1)).isoformat(timespec="seconds")
    if window_end is None:
        window_end = business_now.isoformat(timespec="seconds")
    if measured_at is None:
        measured_at = window_end
    window_start = _normalize_datetime(window_start, timezone_name, "采集窗口开始时间")
    window_end = _normalize_datetime(window_end, timezone_name, "采集窗口结束时间")
    measured_at = _normalize_datetime(measured_at, timezone_name, "指标观测时间")
    if window_start and window_end and window_start > window_end:
        raise OperatingFeedbackError("采集窗口结束时间不能早于开始时间")
    idem = str(idempotency_key or uuid.uuid4().hex).strip()
    if len(idem) > 200:
        raise OperatingFeedbackError("幂等键过长")
    existing = conn.execute(
        "SELECT * FROM review_collection_runs WHERE idempotency_key=?", (idem,)
    ).fetchone()
    if existing:
        if (
            existing["task_id"] != task_id or existing["metrics_json"] != _json(normalized)
            or existing["status"] != status or existing["source"] != source
            or existing["window_start"] != window_start or existing["window_end"] != window_end
            or existing["timezone_name"] != timezone_name
        ):
            raise OperatingFeedbackError("该幂等键已用于不同的采集请求")
        item = dict(existing)
        item["metrics"] = _parse_json(item.pop("metrics_json"), {})
        item["already_recorded"] = True
        return item

    run_id = uuid.uuid4().hex
    now = utc_now()
    observed = measured_at or now
    conn.execute(
        """INSERT INTO review_collection_runs
           (run_id,task_id,source,status,window_start,window_end,timezone_name,metrics_json,
            error_message,idempotency_key,started_at,completed_at,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, task_id, source, status, window_start, window_end, timezone_name,
         _json(normalized), str(error_message or "")[:1000] if status in {"failed", "partial"} else None,
         idem, now, now, now),
    )
    if status in {"succeeded", "partial"}:
        for key, value in normalized.items():
            conn.execute(
                """INSERT INTO metric_facts
                   (fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
                    source_record_type,source_record_id,measured_at,recorded_at,window_start,
                    window_end,timezone_name,collection_status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'review_collection_run',?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, task["tenant_id"], task["draft_id"], task["content_id"],
                 task["platform"], key, value, METRIC_UNITS[key], source, run_id, observed, now,
                 window_start, window_end, timezone_name, status, now),
            )
    new_task_status = "failed" if status == "failed" else ("partial" if status == "partial" else "completed")
    conn.execute(
        """UPDATE review_tasks SET status=?,attempt_count=attempt_count+1,last_attempt_at=?,
           last_success_at=CASE WHEN ? IN ('succeeded','partial') THEN ? ELSE last_success_at END,
           last_window_start=?,last_window_end=?,last_error=?,updated_at=? WHERE task_id=?""",
        (new_task_status, now, status, now, window_start, window_end, error_message or None, now, task_id),
    )
    item = dict(conn.execute("SELECT * FROM review_collection_runs WHERE run_id=?", (run_id,)).fetchone())
    item["metrics"] = _parse_json(item.pop("metrics_json"), {})
    item["already_recorded"] = False
    return item


def retry_review_task(conn, task_id: str, *, scheduled_at: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM review_tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        raise OperatingFeedbackError("复盘任务不存在")
    if row["status"] not in {"failed", "partial"}:
        raise OperatingFeedbackError("只有失败或部分完成的复盘任务可以人工重试")
    now = utc_now()
    due = scheduled_at or now
    conn.execute(
        """UPDATE review_tasks SET status='pending',scheduled_at=?,attempt_count=0,
           collection_cycle_id=?,last_error=NULL,last_success_at=NULL,
           last_window_start=NULL,last_window_end=NULL,conclusion_json='{}',updated_at=?
           WHERE task_id=?""",
        (due, uuid.uuid4().hex, now, task_id),
    )
    return dict(conn.execute("SELECT * FROM review_tasks WHERE task_id=?", (task_id,)).fetchone())


def save_review_conclusion(
    conn, *, task_id: str, conclusion: str, evidence: list | dict,
    target_impact: dict, recommendations: list, gap_changes: list,
) -> dict:
    task = conn.execute("SELECT * FROM review_tasks WHERE task_id=?", (task_id,)).fetchone()
    if task is None:
        raise OperatingFeedbackError("复盘任务不存在")
    if task["status"] not in {"completed", "partial"}:
        raise OperatingFeedbackError("必须先完成至少一次成功或部分成功的数据采集，才能提交复盘结论")
    if not str(conclusion or "").strip():
        raise OperatingFeedbackError("复盘结论不能为空")
    payload = {
        "conclusion": str(conclusion).strip()[:4000],
        "evidence": evidence if isinstance(evidence, (list, dict)) else [],
        "target_impact": target_impact if isinstance(target_impact, dict) else {},
        "recommendations": recommendations if isinstance(recommendations, list) else [],
    }
    now = utc_now()
    conn.execute(
        "UPDATE review_tasks SET conclusion_json=?,updated_at=? WHERE task_id=?",
        (_json(payload), now, task_id),
    )
    changes = gap_changes if isinstance(gap_changes, list) else []
    for change in changes:
        if not isinstance(change, dict):
            continue
        key = str(change.get("gap_key") or "").strip()
        title = str(change.get("title") or "").strip()
        if not key or not title:
            continue
        if len(key) > 160 or len(title) > 160:
            raise OperatingFeedbackError("经营缺口标识和标题不能超过 160 个字符")
        before_row = conn.execute(
            "SELECT * FROM business_gaps WHERE tenant_id=? AND gap_key=?",
            (task["tenant_id"], key),
        ).fetchone()
        before = dict(before_row) if before_row else None
        severity = str(change.get("severity") or (before or {}).get("severity") or "medium")
        status = str(change.get("status") or "active")
        if severity not in {"low", "medium", "high", "critical"} or status not in {"active", "resolved"}:
            raise OperatingFeedbackError("经营缺口等级或状态无效")
        gap_id = before["gap_id"] if before else uuid.uuid4().hex
        description = str(change.get("description") or (before or {}).get("description") or "")[:2000]
        evidence_json = _json(change.get("evidence") if isinstance(change.get("evidence"), (dict, list)) else {})
        if before:
            conn.execute(
                """UPDATE business_gaps SET title=?,description=?,severity=?,status=?,
                   source_review_task_id=?,evidence_json=?,updated_at=? WHERE gap_id=?""",
                (title, description, severity, status, task_id, evidence_json, now, gap_id),
            )
            event_type = "resolved" if status == "resolved" and before["status"] != "resolved" else (
                "reopened" if status == "active" and before["status"] == "resolved" else "updated"
            )
        else:
            conn.execute(
                """INSERT INTO business_gaps
                   (gap_id,tenant_id,gap_key,title,description,severity,status,source_review_task_id,
                    evidence_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (gap_id, task["tenant_id"], key, title, description, severity, status,
                 task_id, evidence_json, now, now),
            )
            event_type = "opened"
        after = dict(conn.execute("SELECT * FROM business_gaps WHERE gap_id=?", (gap_id,)).fetchone())
        conn.execute(
            """INSERT INTO business_gap_events
               (gap_id,review_task_id,event_type,reason,before_json,after_json,evidence_json,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (gap_id, task_id, event_type, str(change.get("reason") or "")[:1000],
             _json(before) if before else None, _json(after), evidence_json, now),
        )
    return {"task_id": task_id, "conclusion": payload, "gap_changes_recorded": len(changes)}


def build_decision_context(conn, *, tenant_id: str, metric_limit: int = 100) -> dict:
    latest = conn.execute(
        """SELECT rt.*,pv.platform_content_id,pv.content_url,pv.published_at,pv.content_snapshot_json
           FROM review_tasks rt JOIN content_publish_verifications pv
             ON pv.verification_id=rt.verification_id
           WHERE rt.tenant_id=? AND rt.status IN ('completed','partial')
           AND rt.conclusion_json!='{}' ORDER BY COALESCE(rt.last_success_at,rt.updated_at) DESC LIMIT 1""",
        (tenant_id,),
    ).fetchone()
    metrics = conn.execute(
        """SELECT * FROM metric_facts_effective WHERE tenant_id IN (?, 'legacy')
           ORDER BY measured_at DESC,recorded_at DESC LIMIT ?""",
        (tenant_id, max(1, min(int(metric_limit), 500))),
    ).fetchall()
    gaps = conn.execute(
        "SELECT * FROM business_gaps WHERE tenant_id=? AND status='active' ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,updated_at DESC",
        (tenant_id,),
    ).fetchall()
    result = {
        "latest_review": None,
        "metric_facts": [dict(row) for row in metrics],
        "active_business_gaps": [dict(row) for row in gaps],
        "generated_at": utc_now(),
    }
    if latest:
        item = dict(latest)
        item["conclusion"] = _parse_json(item.pop("conclusion_json"), {})
        item["content_snapshot"] = _parse_json(item.pop("content_snapshot_json"), {})
        result["latest_review"] = item
    return result
