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


def test_round_detail_timestamp_serializer_distinguishes_utc_columns_from_business_wall_times():
    from zoneinfo import ZoneInfo
    from src.api.response_utils import serialize_round_detail_timestamps

    result, drafts = serialize_round_detail_timestamps(
        {
            "created_at": "2026-11-05 08:15:00",
            "updated_at": "2026-11-05 08:16:00",
            "producing_started_at": "2026-11-05 16:15:00",
            "collecting_metrics_started_at": "2026-11-05 17:00:00",
        },
        [{"created_at": "2026-11-05 08:16:00", "updated_at": "2026-11-05 08:17:00", "saved_at": "2026-11-05 16:20:00"}],
        business_timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert result["created_at"] == "2026-11-05T08:15:00+00:00"
    assert result["updated_at"] == "2026-11-05T08:16:00+00:00"
    assert result["producing_started_at"] == "2026-11-05T16:15:00+08:00"
    assert result["collecting_metrics_started_at"] == "2026-11-05T17:00:00+08:00"
    assert drafts[0]["created_at"] == "2026-11-05T08:16:00+00:00"
    assert drafts[0]["updated_at"] == "2026-11-05T08:17:00+00:00"
    assert drafts[0]["saved_at"] == "2026-11-05T16:20:00+08:00"


def test_round_detail_timestamp_serializer_preserves_explicit_offsets():
    from zoneinfo import ZoneInfo
    from src.api.response_utils import serialize_round_detail_timestamps

    result, drafts = serialize_round_detail_timestamps(
        {"created_at": "2026-11-05T08:15:00Z"},
        [{"created_at": "2026-11-05T08:16:00+00:00"}],
        business_timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert result["created_at"] == "2026-11-05T08:15:00+00:00"
    assert drafts[0]["created_at"] == "2026-11-05T08:16:00+00:00"

