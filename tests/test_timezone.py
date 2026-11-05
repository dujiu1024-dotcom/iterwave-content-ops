from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from src.core.config import Config
from src.core.timezone import business_now, business_timestamp, business_zone


def test_business_now_uses_fixed_shanghai_timezone_without_changing_legacy_naive_storage(cfg):
    cfg.business_timezone = "Asia/Tokyo"
    value = business_now(cfg)
    assert value.tzinfo is None
    assert business_zone(cfg).key == "Asia/Shanghai"
    assert value.hour == datetime.now(ZoneInfo("Asia/Shanghai")).hour


def test_business_timestamp_uses_fixed_business_clock(cfg):
    cfg.business_timezone = "America/Los_Angeles"
    before = datetime.now(ZoneInfo("Asia/Shanghai")).timestamp()
    value = business_timestamp(cfg)
    after = datetime.now(ZoneInfo("Asia/Shanghai")).timestamp()
    assert before <= value <= after


def test_invalid_business_timezone_is_rejected(cfg):
    payload = cfg.model_dump()
    payload["business_timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        Config.model_validate(payload)


def test_business_now_is_stable_across_day_boundary_for_configured_zone(cfg, monkeypatch):
    from datetime import timezone
    from src.core import timezone as timezone_module

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    cfg.business_timezone = "Asia/Shanghai"
    monkeypatch.setattr(timezone_module, "datetime", FrozenDateTime)
    assert business_now(cfg).date().isoformat() == "2026-11-01"
    assert business_now(cfg).hour == 0


def test_business_now_ignores_configured_dst_timezone(cfg, monkeypatch):
    from datetime import timezone
    from src.core import timezone as timezone_module

    instant = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    cfg.business_timezone = "America/New_York"
    monkeypatch.setattr(timezone_module, "datetime", FrozenDateTime)
    value = business_now(cfg)
    assert business_zone(cfg).key == "Asia/Shanghai"
    assert (value.hour, value.minute) == (13, 30)



def test_business_date_predicate_matches_utc_and_legacy_local_rows_across_midnight(cfg):
    import sqlite3
    from src.core.timezone import business_date_predicate

    cfg.business_timezone = "Asia/Shanghai"
    predicate, bounds = business_date_predicate("fetched_at", "2026-11-03", cfg)
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE metrics (fetched_at TEXT)")
    db.executemany(
        "INSERT INTO metrics(fetched_at) VALUES (?)",
        [
            ("2026-11-02T15:59:59+00:00",),  # before Shanghai business day
            ("2026-11-02T16:00:00+00:00",),  # Shanghai 00:00
            ("2026-11-03T07:59:59+00:00",),  # Shanghai 15:59
            ("2026-11-03T15:59:59+00:00",),  # Shanghai 23:59:59
            ("2026-11-03T16:00:00+00:00",),  # next business day
            ("2026-11-02 23:59:59",),        # legacy previous business day
            ("2026-11-03 00:00:00",),        # legacy business-day start
            ("2026-11-03 23:59:59",),        # legacy business-day end
            ("2026-11-04 00:00:00",),        # legacy next business day
        ],
    )
    rows = db.execute(
        "SELECT fetched_at FROM metrics WHERE " + predicate + " ORDER BY fetched_at",
        bounds,
    ).fetchall()
    assert {row[0] for row in rows} == {
        "2026-11-02T16:00:00+00:00",
        "2026-11-03T07:59:59+00:00",
        "2026-11-03T15:59:59+00:00",
        "2026-11-03 00:00:00",
        "2026-11-03 23:59:59",
    }


def test_business_date_predicate_rejects_untrusted_column(cfg):
    from src.core.timezone import business_date_predicate

    with pytest.raises(ValueError, match="invalid timestamp column"):
        business_date_predicate("fetched_at); DROP TABLE metrics; --", "2026-11-03", cfg)



def test_legacy_saved_at_uses_historical_host_local_timezone():
    import os
    import time
    from datetime import timezone
    from src.core.timezone import parse_server_local_legacy_datetime

    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Los_Angeles"
        if hasattr(time, "tzset"):
            time.tzset()
        parsed = parse_server_local_legacy_datetime("2026-11-02 10:00:00")
        assert parsed == datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if hasattr(time, "tzset"):
            time.tzset()


def test_legacy_saved_at_preserves_explicit_timezone_offset():
    from datetime import timezone
    from src.core.timezone import parse_server_local_legacy_datetime

    parsed = parse_server_local_legacy_datetime("2026-11-02T10:00:00+08:00")
    assert parsed == datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc)


def test_business_date_predicate_accepts_qualified_and_coalesce_columns(cfg):
    from src.core.timezone import business_date_predicate

    qualified, _ = business_date_predicate("m.fetched_at", "2026-11-03", cfg)
    coalesced, _ = business_date_predicate(
        "COALESCE(last_seen_at, discovered_at)", "2026-11-03", cfg,
    )
    assert "m.fetched_at" in qualified
    assert "COALESCE(last_seen_at, discovered_at)" in coalesced



def test_scheduler_cron_trigger_uses_fixed_shanghai_timezone():
    from src.core.scheduler import SchedulerEngine

    cfg = SimpleNamespace(
        business_timezone="America/Los_Angeles",
        scheduler=SimpleNamespace(
            job=lambda _job_id: SimpleNamespace(cron="0 8 * * *", interval_minutes=60),
        ),
    )
    engine = SchedulerEngine.__new__(SchedulerEngine)
    engine.cfg = cfg
    trigger = engine._trigger_for("daily_ops")
    assert trigger.timezone.key == "Asia/Shanghai"


def test_business_date_predicate_supports_legacy_naive_utc_created_at(cfg):
    import sqlite3
    from datetime import timezone
    from src.core.timezone import business_date_predicate

    predicate, bounds = business_date_predicate(
        "created_at", "2026-11-03", cfg, legacy_timezone=timezone.utc,
    )
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE rounds (created_at TEXT)")
    db.executemany(
        "INSERT INTO rounds(created_at) VALUES (?)",
        [
            ("2026-11-02 15:59:59",),
            ("2026-11-02 16:00:00",),
            ("2026-11-03 00:00:00",),
            ("2026-11-03 15:59:59",),
            ("2026-11-03 16:00:00",),
            ("2026-11-02T16:00:00+00:00",),
            ("2026-11-03T15:59:59+00:00",),
        ],
    )
    rows = db.execute(
        "SELECT created_at FROM rounds WHERE " + predicate + " ORDER BY created_at",
        bounds,
    ).fetchall()
    assert {row[0] for row in rows} == {
        "2026-11-02 16:00:00", "2026-11-03 00:00:00",
        "2026-11-03 15:59:59", "2026-11-02T16:00:00+00:00",
        "2026-11-03T15:59:59+00:00",
    }


def test_business_date_range_predicate_uses_half_open_shanghai_window_for_utc_and_legacy_naive_rows(cfg):
    import sqlite3
    from datetime import timezone
    from src.core.timezone import business_date_range_predicate

    predicate, bounds = business_date_range_predicate(
        "measured_at", "2026-11-03", "2026-11-05", cfg,
        legacy_timezone=timezone.utc,
    )
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE samples (measured_at TEXT)")
    db.executemany(
        "INSERT INTO samples(measured_at) VALUES (?)",
        [
            ("2026-11-02T15:59:59+00:00",),
            ("2026-11-02T16:00:00+00:00",),
            ("2026-11-04T15:59:59+00:00",),
            ("2026-11-04T16:00:00+00:00",),
            ("2026-11-02 15:59:59",),
            ("2026-11-02 16:00:00",),
            ("2026-11-04 15:59:59",),
            ("2026-11-04 16:00:00",),
        ],
    )
    rows = db.execute(
        "SELECT measured_at FROM samples WHERE " + predicate + " ORDER BY measured_at",
        bounds,
    ).fetchall()
    assert {row[0] for row in rows} == {
        "2026-11-02T16:00:00+00:00", "2026-11-04T15:59:59+00:00",
        "2026-11-02 16:00:00", "2026-11-04 15:59:59",
    }


def test_business_date_range_predicate_rejects_invalid_ranges_and_columns(cfg):
    from datetime import timezone
    from src.core.timezone import business_date_range_predicate

    with pytest.raises(ValueError, match="after start_day"):
        business_date_range_predicate("measured_at", "2026-11-05", "2026-11-05", cfg)
    with pytest.raises(ValueError, match="invalid timestamp column"):
        business_date_range_predicate("measured_at); DROP TABLE x", "2026-11-03", "2026-11-04", cfg, legacy_timezone=timezone.utc)
