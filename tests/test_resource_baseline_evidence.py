import sqlite3

import pytest

from src.core import business_profile_repository
from src.core.resource_baseline_repository import build_resource_baseline


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ({}, "pending"),
        ({"name": {"status": "user_confirmed"}}, "pending"),
        (
            {
                "name": {"status": "user_confirmed"},
                "description": {"status": "external_source"},
            },
            "confirmed",
        ),
        (
            {
                "name": {"status": "user_confirmed"},
                "description": {"status": "ai_inferred"},
            },
            "pending",
        ),
    ],
)
def test_catalog_item_requires_trusted_evidence_for_every_detail_field(
    monkeypatch, evidence, expected
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE business_profiles (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        business_profile_repository,
        "get_snapshot",
        lambda _conn: {
            "profile": {
                "field_evidence": {},
                "business_name": "Example",
                "introduction": "Example profile",
                "industry": "Software",
                "business_model": "Service",
                "operating_goal": "Qualified leads",
                "version_no": 1,
            },
            "items": [
                {
                    "id": "catalog-1",
                    "entity_type": "service",
                    "name": "Consulting",
                    "details": {"name": "Consulting", "description": "Advisory service"},
                    "field_evidence": evidence,
                    "updated_at": "2026-11-05T00:00:00Z",
                }
            ],
        },
    )

    result = build_resource_baseline(conn)
    item = next(
        resource
        for resource in result["resources"]
        if resource["source_type"] == "business_catalog_items"
    )
    assert item["status"] == expected
    conn.close()
