from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from src.core.config import Config
from src.core.timezone import business_now, business_timestamp, business_zone


def test_business_now_uses_configured_timezone_without_changing_legacy_naive_storage(cfg):
    cfg.business_timezone = "Asia/Tokyo"
    value = business_now(cfg)
    assert value.tzinfo is None
    assert business_zone(cfg).key == "Asia/Tokyo"


def test_business_timestamp_uses_configured_timezone(cfg):
    cfg.business_timezone = "Asia/Tokyo"
    before = datetime.now(ZoneInfo("Asia/Tokyo")).timestamp()
    value = business_timestamp(cfg)
    after = datetime.now(ZoneInfo("Asia/Tokyo")).timestamp()
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


@pytest.mark.parametrize(
    ("utc_instant", "expected_hour", "expected_fold"),
    [
        (datetime(2026, 11, 1, 5, 30, tzinfo=ZoneInfo("UTC")), 1, 0),
        (datetime(2026, 11, 1, 6, 30, tzinfo=ZoneInfo("UTC")), 1, 1),
    ],
)
def test_business_now_preserves_dst_fall_back_occurrence(
    cfg, monkeypatch, utc_instant, expected_hour, expected_fold,
):
    from src.core import timezone as timezone_module

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_instant.astimezone(tz) if tz else utc_instant.replace(tzinfo=None)

    cfg.business_timezone = "America/New_York"
    monkeypatch.setattr(timezone_module, "datetime", FrozenDateTime)
    value = business_now(cfg)
    assert value.hour == expected_hour
    assert value.fold == expected_fold


def test_business_now_skips_nonexistent_local_hour_on_dst_spring_forward(cfg, monkeypatch):
    from datetime import timezone
    from src.core import timezone as timezone_module

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    cfg.business_timezone = "America/New_York"
    monkeypatch.setattr(timezone_module, "datetime", FrozenDateTime)
    value = business_now(cfg)
    assert (value.hour, value.minute) == (3, 30)
