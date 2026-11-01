from datetime import timezone
from zoneinfo import ZoneInfo

from src.api.main import _api_datetime


def test_api_datetime_interprets_tenant_registry_naive_time_as_business_local():
    value = _api_datetime(
        "2026-10-31T01:05:00",
        naive_timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert value == "2026-10-31T01:05:00+08:00"


def test_api_datetime_interprets_sqlite_round_time_as_utc():
    value = _api_datetime("2026-10-31 17:06:00")
    assert value == "2026-10-31T17:06:00+00:00"


def test_api_datetime_preserves_explicit_timezone():
    value = _api_datetime(
        "2026-10-31T01:05:00+08:00",
        naive_timezone=timezone.utc,
    )
    assert value == "2026-10-31T01:05:00+08:00"
