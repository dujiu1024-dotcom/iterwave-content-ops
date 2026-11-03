"""Business timezone and canonical UTC persistence helpers."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
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


def storage_window_cutoffs(utc_cutoff, cfg=None) -> tuple[str, str]:
    """Return UTC and legacy business-local cutoffs for mixed-format timestamp columns."""
    parsed = parse_persisted_datetime(utc_cutoff, cfg, legacy_timezone=timezone.utc)
    if parsed is None:
        raise ValueError("invalid timestamp cutoff")
    utc_value = parsed.isoformat(sep=" ", timespec="seconds")
    local_value = parsed.astimezone(business_zone(cfg)).replace(tzinfo=None).isoformat(
        sep=" ", timespec="seconds",
    )
    return utc_value, local_value


def timestamp_since_sql(column: str) -> str:
    """SQLite predicate for UTC-aware new rows plus legacy business-local naive rows."""
    if not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?|COALESCE\([A-Za-z_][A-Za-z0-9_]*,\s*[A-Za-z_][A-Za-z0-9_]*\))", column):
        raise ValueError("invalid timestamp column")
    aware = (
        f"(instr(substr({column}, 20), '+') > 0 "
        f"OR instr(substr({column}, 20), '-') > 0 OR substr({column}, -1) = 'Z')"
    )
    return (
        f"(({aware} AND datetime({column}) >= datetime(?)) "
        f"OR (NOT {aware} AND datetime({column}) >= datetime(?)))"
    )


def timestamp_before_sql(column: str) -> str:
    """SQLite predicate for timestamps at or before a cutoff in mixed legacy/new formats."""
    if not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?|COALESCE\([A-Za-z_][A-Za-z0-9_]*,\s*[A-Za-z_][A-Za-z0-9_]*\))", column):
        raise ValueError("invalid timestamp column")
    aware = (
        f"(instr(substr({column}, 20), '+') > 0 "
        f"OR instr(substr({column}, 20), '-') > 0 OR substr({column}, -1) = 'Z')"
    )
    return (
        f"(({aware} AND datetime({column}) <= datetime(?)) "
        f"OR (NOT {aware} AND datetime({column}) <= datetime(?)))"
    )


def business_date_cutoffs(day: str, cfg=None, *, end: bool = False) -> tuple[str, str]:
    """UTC and legacy-local cutoffs for an inclusive business-calendar date."""
    try:
        parsed_day = date.fromisoformat(str(day))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid business date") from exc
    local_midnight = datetime.combine(parsed_day, time.min, tzinfo=business_zone(cfg))
    if end:
        next_day = parsed_day + timedelta(days=1)
        local_midnight = datetime.combine(next_day, time.min, tzinfo=business_zone(cfg)) - timedelta(seconds=1)
    utc_value = local_midnight.astimezone(timezone.utc).isoformat(sep=" ", timespec="seconds")
    legacy_value = local_midnight.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    return utc_value, legacy_value



def business_date_predicate(column: str, day: str, cfg=None) -> tuple[str, tuple[str, str, str, str]]:
    """Return a mixed-format SQL predicate and bounds for one business calendar day.

    Offset-aware rows are compared as UTC instants; legacy naive rows are interpreted
    as business-local wall time. The inclusive end bound is second-precision to match
    the persisted timestamp formats used by this application.
    """
    if not re.fullmatch(
        r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?|"
        r"COALESCE\([A-Za-z_][A-Za-z0-9_]*,\s*[A-Za-z_][A-Za-z0-9_]*\))",
        column,
    ):
        raise ValueError("invalid timestamp column")
    utc_start, legacy_start = business_date_cutoffs(day, cfg)
    utc_end, legacy_end = business_date_cutoffs(day, cfg, end=True)
    aware = (
        f"(instr(substr(COALESCE({column}, ''), 20), '+') > 0 "
        f"OR instr(substr(COALESCE({column}, ''), 20), '-') > 0 "
        f"OR substr(COALESCE({column}, ''), -1) = 'Z')"
    )
    sql = (
        f"(({aware} AND datetime({column}) BETWEEN datetime(?) AND datetime(?)) "
        f"OR (NOT {aware} AND datetime({column}) BETWEEN datetime(?) AND datetime(?)))"
    )
    return sql, (utc_start, utc_end, legacy_start, legacy_end)



def parse_server_local_legacy_datetime(value) -> datetime | None:
    """Parse pre-UTC naive state-machine timestamps written in the host's local timezone.

    This is intentionally separate from parse_persisted_datetime: only columns with
    confirmed historical server-local semantics should use this compatibility path.
    Explicitly offset timestamps always retain their encoded instant.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    # datetime.timestamp() interprets a naive value in the process' local zone,
    # including the host timezone's historical DST rules.
    try:
        return datetime.fromtimestamp(parsed.timestamp(), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
