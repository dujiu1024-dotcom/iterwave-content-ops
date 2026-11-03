from __future__ import annotations

import sqlite3

import pytest

from src.core.strategy_step_completion import (
    assert_stage_completion_ready,
    completion_gate_state,
)


def _stage(**overrides):
    value = {
        "target_count": 5,
        "confirmed_published_count": 5,
        "business_completion_required": 0,
        "business_completion_status": "not_required",
        "effect_required": 0,
        "effect_status": "not_required",
    }
    value.update(overrides)
    return value


def test_quantity_target_alone_can_complete_when_no_other_gates_are_declared():
    state = completion_gate_state(_stage())
    assert state["quantity_passed"] is True
    assert state["can_complete"] is True


def test_quantity_target_does_not_replace_business_confirmation():
    state = completion_gate_state(_stage(
        business_completion_required=1,
        business_completion_status="pending",
    ))
    assert state["quantity_passed"] is True
    assert state["can_complete"] is False
    assert "business_completion_pending" in state["blockers"]


def test_effect_failure_blocks_completion_and_further_production():
    state = completion_gate_state(_stage(
        effect_required=1,
        effect_status="failed",
    ))
    assert state["can_complete"] is False
    assert state["production_blocked"] is True


def test_failed_or_review_pending_business_gate_blocks_production():
    for status in ("failed", "needs_review"):
        state = completion_gate_state(_stage(
            business_completion_required=1,
            business_completion_status=status,
            confirmed_published_count=2,
        ))
        assert state["production_blocked"] is True


def test_completion_assertion_requires_quantity_and_all_required_gates():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sub_plan_stages (
            id INTEGER PRIMARY KEY,
            target_count INTEGER NOT NULL,
            business_completion_required INTEGER NOT NULL,
            business_completion_status TEXT NOT NULL,
            effect_required INTEGER NOT NULL,
            effect_status TEXT NOT NULL
        );
        CREATE TABLE sub_plan_stage_publish_stats (
            stage_id INTEGER PRIMARY KEY,
            confirmed_published_count INTEGER NOT NULL
        );
        INSERT INTO sub_plan_stages VALUES (1, 5, 1, 'pending', 0, 'not_required');
        INSERT INTO sub_plan_stage_publish_stats VALUES (1, 5);
    """)
    with pytest.raises(ValueError, match="业务完成条件尚未确认"):
        assert_stage_completion_ready(conn, 1)

    conn.execute(
        "UPDATE sub_plan_stages SET business_completion_status='passed' WHERE id=1"
    )
    assert assert_stage_completion_ready(conn, 1)["completion_gates"]["can_complete"] is True

    conn.execute(
        "UPDATE sub_plan_stage_publish_stats SET confirmed_published_count=4 WHERE stage_id=1"
    )
    with pytest.raises(ValueError, match="发布数量目标尚未达标"):
        assert_stage_completion_ready(conn, 1)


def test_disabled_review_gates_do_not_block_production():
    state = completion_gate_state(_stage(
        business_completion_required=0,
        business_completion_status="failed",
        effect_required=0,
        effect_status="needs_review",
    ))
    assert state["can_complete"] is True
    assert state["production_blocked"] is False
