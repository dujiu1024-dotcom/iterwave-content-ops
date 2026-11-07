from __future__ import annotations

import pytest

from src.core.visual_planning import build_visual_brief, build_visual_plan


def _brief(**overrides):
    values = {
        "platform": "xhs",
        "topic": "AI教育",
        "title": "AI学习的三个步骤",
        "subtitle": "从理解到实践",
        "goal": "education",
        "audience": "职场新人",
        "key_message": "先建立基础，再进行实践",
        "call_to_action": "收藏并按步骤练习",
        "source_facts": ["课程分为三个阶段", "每周练习两次"],
        "brand_profile": {"primary_color": "#123456", "untrusted_extra": "must not persist"},
    }
    values.update(overrides)
    return build_visual_brief(**values)


def test_visual_brief_normalizes_explicit_goal_and_whitelists_brand_rules():
    brief = _brief()
    assert brief.version == "visual-brief-v1"
    assert brief.goal == "education"
    assert brief.audience == "职场新人"
    assert brief.source_facts == ("课程分为三个阶段", "每周练习两次")
    assert dict(brief.brand_rules) == {"primary_color": "#123456"}


def test_visual_plan_has_cover_body_and_ending_in_order_and_preserves_body_text():
    brief = _brief()
    body = ["第一步：理解基础概念", "第二步：每周练习两次"]
    plan = build_visual_plan(
        brief=brief,
        title="AI学习的三个步骤",
        subtitle="从理解到实践",
        body_pages=body,
        reference_asset_ids={"cover": [3, 3, -1], "body": [4], "ending": [5]},
    )
    assert [page.role for page in plan.pages] == ["cover", "body", "body", "ending"]
    assert plan.pages[1].source_text == body[0]
    assert plan.pages[2].source_text == body[1]
    assert plan.pages[1].visual_pattern == "process"
    assert plan.pages[0].reference_asset_ids == (3,)
    assert plan.pages[-1].reference_asset_ids == (5,)
    assert plan.pages[-1].source_text == "先建立基础，再进行实践\n收藏并按步骤练习"
    assert plan.page_count == 4


def test_visual_plan_does_not_invent_a_call_to_action():
    brief = _brief(call_to_action="")
    plan = build_visual_plan(
        brief=brief,
        title="AI学习的三个步骤",
        subtitle="从理解到实践",
        body_pages=["先建立基础。"],
    )
    ending = plan.pages[-1]
    assert ending.visual_pattern == "editorial_conclusion"
    assert ending.source_text == "先建立基础，再进行实践"
    assert "不补写未提供" in ending.visual_direction


def test_visual_brief_rejects_unknown_platform_or_goal():
    with pytest.raises(ValueError, match="unsupported_visual_platform"):
        _brief(platform="unknown")
    with pytest.raises(ValueError, match="unsupported_visual_goal"):
        _brief(goal="guaranteed_sales")


def test_visual_plan_requires_title_and_body_pages():
    brief = _brief()
    with pytest.raises(ValueError, match="visual_title_required"):
        build_visual_plan(brief=brief, title="", subtitle="", body_pages=["text"])
    with pytest.raises(ValueError, match="visual_body_pages_required"):
        build_visual_plan(brief=brief, title="Title", subtitle="", body_pages=["", "  "])
