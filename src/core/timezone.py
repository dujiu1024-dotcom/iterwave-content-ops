"""Business timezone and canonical UTC persistence helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"


def business_zone(cfg=None) -> ZoneInfo:
    name = getattr(cfg, "business_timezone", None) or DEFAULT_TIMEZONE
    return ZoneInfo(str(name))


def business_now(cfg=None) -> datetime:
    """Return naive wall-clock datetime for business-calendar calculations only."""
    return datetime.now(business_zone(cfg)).replace(tzinfo=None)


def business_timestamp(cfg=None) -> float:
    """Unix timestamp for the configured business clock (DST-safe)."""
    return datetime.now(business_zone(cfg)).timestamp()


def business_today(cfg=None):
    return business_now(cfg).date()


def business_from_timestamp(value: float, cfg=None) -> datetime:
    """Convert an epoch timestamp to the configured business timezone as a naive local value."""
    return datetime.fromtimestamp(float(value), tz=business_zone(cfg)).replace(tzinfo=None)


def storage_now() -> datetime:
    """Return an aware UTC instant for all new persisted timestamps."""
    return datetime.now(timezone.utc)


def storage_timestamp(*, timespec: str = "microseconds") -> str:
    """Canonical ISO-8601 UTC timestamp for database persistence."""
    return storage_now().isoformat(timespec=timespec)


def normalize_storage_timestamp(value, *, legacy_timezone=timezone.utc) -> str | None:
    """Normalize an incoming timestamp to UTC; naive legacy values need an explicit zone."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid datetime value") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=legacy_timezone)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_persisted_datetime(value, cfg=None, *, legacy_timezone=None) -> datetime | None:
    """Parse persisted timestamps as UTC instants; legacy naive values use business timezone."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=legacy_timezone or business_zone(cfg))
    return parsed.astimezone(timezone.utc)
