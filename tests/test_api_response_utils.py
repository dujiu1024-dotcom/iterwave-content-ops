from datetime import timedelta, timezone

from src.api.response_utils import api_datetime, friendly_validation_messages


def test_api_datetime_adds_timezone_to_legacy_naive_values():
    assert api_datetime("2026-11-02T12:30:00") == "2026-11-02T12:30:00+00:00"
    assert api_datetime("2026-11-02T12:30:00Z") == "2026-11-02T12:30:00+00:00"
    assert api_datetime("not-a-date") == "not-a-date"
    assert api_datetime(None) is None


def test_api_datetime_accepts_explicit_business_timezone():
    assert api_datetime(
        "2026-11-02T12:30:00",
        naive_timezone=timezone(timedelta(hours=8)),
    ) == "2026-11-02T12:30:00+08:00"


def test_friendly_validation_messages_never_echo_raw_error_input():
    messages = friendly_validation_messages([
        {"loc": ("body", "name"), "type": "missing"},
        {"loc": ("body", "count"), "type": "greater_than_equal", "ctx": {"ge": 1}},
        {"loc": ("body", "secret"), "type": "unknown", "input": "do-not-echo"},
    ])

    assert messages == ["名称：不能为空", "请求参数：不能小于 1", "请求参数：内容不符合要求"]
    assert all("do-not-echo" not in message for message in messages)
    assert friendly_validation_messages([]) == ["请求参数不符合要求"]
