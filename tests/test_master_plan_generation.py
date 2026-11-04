from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from src.core.master_plan_generation import (
    MasterPlanGenerationError,
    generate_master_plan_draft,
    parse_generation_response,
    validate_master_plan,
)


def test_parse_generation_response_accepts_json_object():
    result = parse_generation_response('prefix {"plan": {"topic_count_per_platform": 5}} trailing')
    assert result["plan"]["topic_count_per_platform"] == 5


@pytest.mark.parametrize("raw", [None, "no json here", '{"other": true}', '{"plan": []}'])
def test_parse_generation_response_rejects_invalid_payload(raw):
    with pytest.raises(MasterPlanGenerationError):
        parse_generation_response(raw)


def test_validate_master_plan_uses_existing_schema():
    cfg = SimpleNamespace()
    result = validate_master_plan({
        "topic_count_per_platform": 5,
        "dimension_allocation": {"产品体验": 3, "客户案例": 2},
        "enabled_platforms": ["xhs", "douyin"],
        "content_format": "image_text",
        "per_platform": {"xhs": {"topic_count": 4, "publish_slots": ["09:00", "20:30"]}},
        "cadence": {"round_interval_days": 7, "rounds_planned": 8},
    }, cfg)
    assert result["enabled_platforms"] == ["douyin", "xhs"]
    assert result["per_platform"]["xhs"]["topic_count"] == 4


@pytest.mark.parametrize("plan", [
    {"topic_count_per_platform": 0, "enabled_platforms": ["xhs"]},
    {"topic_count_per_platform": 5, "enabled_platforms": []},
    {"topic_count_per_platform": 5, "enabled_platforms": ["unknown"]},
    {"topic_count_per_platform": 5, "enabled_platforms": ["xhs"], "dimension_allocation": {"a": 6}},
    {"topic_count_per_platform": 5, "enabled_platforms": ["xhs"], "per_platform": {"douyin": {"topic_count": 2}}},
    {"topic_count_per_platform": 5, "enabled_platforms": ["xhs"], "per_platform": {"xhs": {"publish_slots": ["25:90"]}}},
    {"topic_count_per_platform": 5, "enabled_platforms": ["xhs"], "content_format": "video"},
])
def test_validate_master_plan_rejects_invalid_schema(plan):
    with pytest.raises(MasterPlanGenerationError):
        validate_master_plan(plan, SimpleNamespace())


def test_generation_rejects_missing_diagnosis_before_model_call(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE business_diagnosis_reports (id INTEGER, version_no INTEGER, status TEXT)")
    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError("model must not be called without a confirmed diagnosis")
    monkeypatch.setattr("src.core.master_plan_generation._call_model", unexpected_model_call)
    with pytest.raises(LookupError, match="尚无经营诊断报告"):
        asyncio.run(generate_master_plan_draft(conn, SimpleNamespace()))
    conn.close()


def test_generation_rejects_unconfirmed_diagnosis_before_context_read(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE business_diagnosis_reports (id INTEGER, version_no INTEGER, status TEXT)")
    conn.execute("INSERT INTO business_diagnosis_reports VALUES (1, 1, 'draft')")
    def unexpected_context_read(*args, **kwargs):
        raise AssertionError("unconfirmed diagnosis must be rejected before context/model work")
    monkeypatch.setattr("src.core.business_diagnosis.collect_business_context", unexpected_context_read)
    with pytest.raises(LookupError, match="必须先确认报告"):
        asyncio.run(generate_master_plan_draft(conn, SimpleNamespace()))
    conn.close()


def test_generation_model_failure_does_not_create_a_draft(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE business_diagnosis_reports (
        id INTEGER, version_no INTEGER, status TEXT, source_fingerprint TEXT,
        report_json TEXT, source_snapshot_json TEXT
    )""")
    conn.execute(
        "INSERT INTO business_diagnosis_reports VALUES (1, 1, 'confirmed', 'fp', '{}', '{}')"
    )
    conn.execute("CREATE TABLE master_plan_versions (id INTEGER, status TEXT, deleted_at TEXT)")
    monkeypatch.setattr(
        "src.core.business_diagnosis.collect_business_context",
        lambda *_: {"fingerprint": "fp", "context": {}},
    )
    monkeypatch.setattr(
        "src.core.plan_version.PlanVersionManager.load_plan",
        lambda *_: ({}, "", None, "derived"),
    )
    async def provider_failure(*args, **kwargs):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr("src.core.master_plan_generation._call_model", provider_failure)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(generate_master_plan_draft(conn, SimpleNamespace()))
    assert conn.execute("SELECT COUNT(*) FROM master_plan_versions").fetchone()[0] == 0
    conn.close()


def test_generation_rejects_stale_source_before_model_call(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE business_diagnosis_reports (
        id INTEGER, version_no INTEGER, status TEXT, source_fingerprint TEXT
    )""")
    conn.execute("INSERT INTO business_diagnosis_reports VALUES (1, 1, 'confirmed', 'old')")
    monkeypatch.setattr(
        "src.core.business_diagnosis.collect_business_context",
        lambda *_: {"fingerprint": "new"},
    )
    async def unexpected_model_call(*args, **kwargs):
        raise AssertionError("stale diagnosis must be rejected before model invocation")
    monkeypatch.setattr("src.core.master_plan_generation._call_model", unexpected_model_call)
    with pytest.raises(LookupError, match="已过期"):
        asyncio.run(generate_master_plan_draft(conn, SimpleNamespace()))
    assert conn.execute("SELECT status FROM business_diagnosis_reports").fetchone()[0] == "stale"
    conn.close()
