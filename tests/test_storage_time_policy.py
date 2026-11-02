from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.api.response_utils import api_datetime
from src.core.timezone import (
    normalize_storage_timestamp, parse_persisted_datetime, storage_now, storage_timestamp,
)


def test_storage_now_is_timezone_aware_utc():
    value = storage_now()
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0


def test_storage_timestamp_uses_canonical_utc_iso8601():
    value = storage_timestamp(timespec="seconds")
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert value.endswith("+00:00")


def test_normalize_storage_timestamp_converts_explicit_offset_to_utc():
    value = normalize_storage_timestamp("2026-11-02T10:00:00+08:00")
    assert value == "2026-11-02T02:00:00.000000+00:00"


def test_normalize_storage_timestamp_requires_explicit_legacy_zone():
    value = normalize_storage_timestamp(
        "2026-11-02 10:00:00", legacy_timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert value == "2026-11-02T02:00:00.000000+00:00"


def test_normalize_storage_timestamp_rejects_malformed_values():
    with pytest.raises(ValueError, match="invalid datetime"):
        normalize_storage_timestamp("not-a-date")


def test_normalize_storage_timestamp_preserves_null():
    assert normalize_storage_timestamp(None) is None
    assert normalize_storage_timestamp("") is None


def test_parse_persisted_datetime_reads_legacy_naive_as_business_timezone():
    value = parse_persisted_datetime("2026-11-02 10:00:00")
    assert value == datetime(2026, 11, 2, 2, tzinfo=timezone.utc)


def test_parse_persisted_datetime_normalizes_explicit_offsets():
    value = parse_persisted_datetime("2026-11-02T10:00:00+08:00")
    assert value == datetime(2026, 11, 2, 2, tzinfo=timezone.utc)


def test_round_and_draft_state_machines_persist_utc_timestamps(cfg, conn, seed):
    import json

    from src.core.state_machine import DraftStateMachine, RoundStateMachine

    round_id = seed.round(status="created")
    assert RoundStateMachine(conn, cfg=cfg).transition(round_id, "producing", "test")
    round_row = conn.execute(
        "SELECT producing_started_at, transition_log FROM spiral_rounds WHERE id=?", (round_id,),
    ).fetchone()
    assert datetime.fromisoformat(round_row["producing_started_at"]).utcoffset().total_seconds() == 0
    transition = json.loads(round_row["transition_log"])[-1]
    assert datetime.fromisoformat(transition["at"]).utcoffset().total_seconds() == 0

    draft_id = seed.draft(
        round_id, platform="xhs", draft_status="draft_pending", fetch_status=None,
    )
    assert DraftStateMachine(conn, cfg=cfg).transition(draft_id, "draft_saved", "test save")
    saved_at = conn.execute("SELECT saved_at FROM drafts WHERE id=?", (draft_id,)).fetchone()[0]
    assert datetime.fromisoformat(saved_at).utcoffset().total_seconds() == 0



def test_api_datetime_uses_utc_for_generic_legacy_naive_timestamp():
    assert api_datetime("2026-11-02 10:00:00") == "2026-11-02T10:00:00+00:00"


def test_api_datetime_accepts_explicit_business_timezone_for_legacy_round_data():
    from zoneinfo import ZoneInfo
    assert api_datetime(
        "2026-11-02 10:00:00", naive_timezone=ZoneInfo("Asia/Shanghai"),
    ) == "2026-11-02T10:00:00+08:00"


def test_api_datetime_preserves_explicit_offset():
    assert api_datetime("2026-11-02T10:00:00+08:00") == "2026-11-02T10:00:00+08:00"


def test_api_datetime_preserves_unparseable_values():
    assert api_datetime("not-a-date") == "not-a-date"