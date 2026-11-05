from datetime import timezone
from zoneinfo import ZoneInfo

from src.api.response_utils import serialize_round_detail_timestamps


def test_round_detail_serializes_sqlite_created_at_as_utc_and_legacy_phase_time_as_shanghai():
    round_data = {
        "created_at": "2026-11-05 08:15:00",
        "producing_started_at": "2026-11-05 16:15:00",
        "collecting_metrics_started_at": "2026-11-05 17:00:00",
    }
    drafts = [{
        "id": "draft-1",
        "created_at": "2026-11-05 08:16:00",
        "saved_at": "2026-11-05 16:20:00",
    }]

    result, serialized_drafts = serialize_round_detail_timestamps(
        round_data, drafts, business_timezone=ZoneInfo("Asia/Shanghai"),
    )

    assert result["created_at"] == "2026-11-05T08:15:00+00:00"
    assert result["producing_started_at"] == "2026-11-05T16:15:00+08:00"
    assert result["collecting_metrics_started_at"] == "2026-11-05T17:00:00+08:00"
    assert serialized_drafts[0]["created_at"] == "2026-11-05T08:16:00+00:00"
    assert serialized_drafts[0]["saved_at"] == "2026-11-05T16:20:00+08:00"


def test_round_detail_preserves_explicit_timestamp_offsets():
    result, drafts = serialize_round_detail_timestamps(
        {"created_at": "2026-11-05T08:15:00Z"},
        [{"created_at": "2026-11-05T08:16:00+00:00"}],
        business_timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert result["created_at"] == "2026-11-05T08:15:00+00:00"
    assert drafts[0]["created_at"] == "2026-11-05T08:16:00+00:00"
