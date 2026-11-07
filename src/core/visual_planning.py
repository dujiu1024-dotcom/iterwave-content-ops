"""Structured visual brief and page-role planning for article image sets.

The planner is deterministic: it organizes supplied content and explicit business
intent without inventing claims, proof points, or calls to action.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .visual_specs import VisualComponent, build_infographic_components


VISUAL_BRIEF_VERSION = "visual-brief-v1"
VISUAL_PLAN_VERSION = "visual-plan-v1"
SUPPORTED_PLATFORMS = {"xhs", "douyin", "kuaishou", "wechat_channels"}
SUPPORTED_GOALS = {"awareness", "education", "conversion", "engagement", "brand", "informational"}


@dataclass(frozen=True)
class VisualBrief:
    version: str
    platform: str
    topic: str
    goal: str
    audience: str
    key_message: str
    call_to_action: str
    tone: str
    source_facts: tuple[str, ...]
    brand_rules: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class VisualPagePlan:
    page_number: int
    role: str
    purpose: str
    headline: str
    source_text: str
    visual_pattern: str
    visual_direction: str
    components: tuple[VisualComponent, ...]
    reference_asset_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class VisualPlan:
    version: str
    brief: VisualBrief
    pages: tuple[VisualPagePlan, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "brief": asdict(self.brief),
            "page_count": self.page_count,
            "pages": [
                {
                    **{
                        key: value
                        for key, value in asdict(page).items()
                        if key != "components"
                    },
                    "components": [asdict(component) for component in page.components],
                }
                for page in self.pages
            ],
        }


def _clean_text(value: object, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _positive_ids(values: Sequence[object] | None) -> tuple[int, ...]:
    result: list[int] = []
    for value in values or ():
        try:
            asset_id = int(value)
        except (TypeError, ValueError):
            continue
        if asset_id > 0 and asset_id not in result:
            result.append(asset_id)
    return tuple(result[:8])


def build_visual_brief(
    *,
    platform: str,
    topic: str,
    title: str,
    subtitle: str = "",
    goal: str = "informational",
    audience: str = "",
    key_message: str = "",
    call_to_action: str = "",
    tone: str = "",
    source_facts: Sequence[str] | None = None,
    brand_profile: Mapping[str, object] | None = None,
) -> VisualBrief:
    """Build a normalized brief using only explicit content and business inputs."""
    platform_key = _clean_text(platform, limit=40).lower()
    goal_key = _clean_text(goal, limit=40).lower()
    if platform_key not in SUPPORTED_PLATFORMS:
        raise ValueError("unsupported_visual_platform")
    if goal_key not in SUPPORTED_GOALS:
        raise ValueError("unsupported_visual_goal")
    allowed_brand_keys = (
        "brand_name", "primary_color", "secondary_color", "background_color",
        "text_color", "style_key", "component_style",
    )
    brand_rules = tuple(
        (key, _clean_text((brand_profile or {}).get(key), limit=160))
        for key in allowed_brand_keys
        if _clean_text((brand_profile or {}).get(key), limit=160)
    )
    facts = tuple(
        dict.fromkeys(_clean_text(item, limit=500) for item in (source_facts or ())
                      if _clean_text(item, limit=500))
    )[:20]
    normalized_title = _clean_text(title, limit=300)
    if not normalized_title:
        raise ValueError("visual_title_required")
    return VisualBrief(
        version=VISUAL_BRIEF_VERSION,
        platform=platform_key,
        topic=_clean_text(topic, limit=500),
        goal=goal_key,
        audience=_clean_text(audience, limit=500),
        key_message=_clean_text(key_message, limit=1000) or normalized_title,
        call_to_action=_clean_text(call_to_action, limit=500),
        tone=_clean_text(tone, limit=200),
        source_facts=facts,
        brand_rules=brand_rules,
    )


def _pattern(components: Sequence[VisualComponent]) -> str:
    kinds = {item.kind for item in components}
    if "step" in kinds:
        return "process"
    if "stat" in kinds:
        return "data_highlight"
    if "quote" in kinds:
        return "quote_feature"
    return "editorial_explainer"


def _goal_direction(goal: str) -> str:
    return {
        "awareness": "优先建立主题识别和清晰记忆点，不添加未经提供的效果承诺",
        "education": "优先解释概念、步骤和因果关系，信息层级清晰",
        "conversion": "突出已提供的产品价值与明确行动入口，不虚构价格、效果或稀缺性",
        "engagement": "突出可讨论的问题或互动入口，仅使用明确提供的互动文案",
        "brand": "强化已提供的品牌识别规范，不擅自增加品牌标识或口号",
        "informational": "优先保证信息准确、易读和结构清晰",
    }[goal]


def build_visual_plan(
    *,
    brief: VisualBrief,
    title: str,
    subtitle: str,
    body_pages: Sequence[str],
    reference_asset_ids: Mapping[str, Sequence[object]] | None = None,
) -> VisualPlan:
    """Create cover/body/ending roles while preserving every supplied body page."""
    normalized_title = _clean_text(title, limit=300)
    normalized_subtitle = _clean_text(subtitle, limit=300)
    if not normalized_title:
        raise ValueError("visual_title_required")
    normalized_pages = [_clean_text(page, limit=12000) for page in body_pages]
    normalized_pages = [page for page in normalized_pages if page]
    if not normalized_pages:
        raise ValueError("visual_body_pages_required")
    references = reference_asset_ids or {}
    goal_direction = _goal_direction(brief.goal)
    pages: list[VisualPagePlan] = [
        VisualPagePlan(
            page_number=1,
            role="cover",
            purpose="建立主题识别并呈现核心信息",
            headline=normalized_title,
            source_text="\n".join(item for item in (normalized_title, normalized_subtitle) if item),
            visual_pattern="hero_editorial",
            visual_direction=(
                "设置单一明确主视觉、醒目的标题层级和克制的辅助信息；"
                + goal_direction
            ),
            components=(),
            reference_asset_ids=_positive_ids(references.get("cover")),
        )
    ]
    for index, page_text in enumerate(normalized_pages, start=1):
        components = tuple(build_infographic_components([page_text]))
        pattern = _pattern(components)
        direction = {
            "process": "按步骤或阶段建立连续流程，突出顺序关系",
            "data_highlight": "突出原文中的数字和指标，保留单位与上下文",
            "quote_feature": "以引用内容为视觉焦点，保持引用文字完整",
            "editorial_explainer": "按概念和解释建立清晰分组，避免整页文字堆叠",
        }[pattern]
        pages.append(VisualPagePlan(
            page_number=index + 1,
            role="body",
            purpose="完整解释本页正文并保留来源信息",
            headline="",
            source_text=page_text,
            visual_pattern=pattern,
            visual_direction=direction + "；" + goal_direction,
            components=components,
            reference_asset_ids=_positive_ids(references.get("body")),
        ))
    ending_text = brief.key_message
    if brief.call_to_action:
        ending_text = "\n".join((ending_text, brief.call_to_action))
    pages.append(VisualPagePlan(
        page_number=len(pages) + 1,
        role="ending",
        purpose="收束核心信息并呈现明确的下一步",
        headline=brief.key_message,
        source_text=ending_text,
        visual_pattern="conclusion_and_action" if brief.call_to_action else "editorial_conclusion",
        visual_direction=(
            "突出核心结论，留出明确的视觉收束空间；"
            + ("将已提供的行动文案作为主要行动入口" if brief.call_to_action else "不补写未提供的行动号召")
        ),
        components=tuple(build_infographic_components([ending_text])),
        reference_asset_ids=_positive_ids(references.get("ending")),
    ))
    return VisualPlan(version=VISUAL_PLAN_VERSION, brief=brief, pages=tuple(pages))
