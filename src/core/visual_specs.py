"""Versioned, centralized visual output specifications for supported platforms."""
from __future__ import annotations

from dataclasses import dataclass
import re

from ..adapters.base import Platform


@dataclass(frozen=True)
class VisualPlatformSpec:
    key: str
    width: int
    height: int
    max_pages: int = 9
    output_format: str = "png"
    safe_margin_x: int = 92
    safe_margin_top: int = 112
    safe_margin_bottom: int = 120

    @property
    def aspect_ratio(self) -> str:
        return f"{self.width}:{self.height}"


# Conservative common portrait-card baseline. Platform-specific values are centralized
# here so verified platform requirements can be updated without changing layout code.
PLATFORM_SPECS: dict[Platform, VisualPlatformSpec] = {
    Platform.XHS: VisualPlatformSpec("xhs", 1080, 1440),
    Platform.DOUYIN: VisualPlatformSpec("douyin", 1080, 1440),
    Platform.KUAISHOU: VisualPlatformSpec("kuaishou", 1080, 1440),
    Platform.WECHAT_CHANNELS: VisualPlatformSpec("wechat_channels", 1080, 1440),
}


def get_visual_platform_spec(platform: Platform) -> VisualPlatformSpec:
    return PLATFORM_SPECS.get(platform, PLATFORM_SPECS[Platform.XHS])


@dataclass(frozen=True)
class VisualComponent:
    """Semantic infographic component inferred from an authored paragraph."""

    kind: str
    text: str
    value: str = ""
    label: str = ""


_STEP_PREFIX = re.compile(
    r"^(第[一二三四五六七八九十0-9]+(?:步|阶段|点|件事|条|个))\s*[：:、-]?\s*(.*)$"
)
# "1. xxx" / "2、xxx" / "3）xxx" ordered list items (ordinal kept as the step label).
_ORDINAL_PREFIX = re.compile(r"^([0-9]{1,2})\s*[\.、．)）]\s*(\S.*)$")
NUMBER_TOKEN = re.compile(
    r"(?<![0-9,.])([+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?%?)(?![0-9])"
)


def build_infographic_components(paragraphs: list[str]) -> list[VisualComponent]:
    """Map source paragraphs to safe, deterministic visual components without rewriting copy.

    Numeric claims become inline stat highlights, ordered process headings become step
    cards, quoted statements become quote panels, and all remaining copy stays a text
    component. Text content is preserved byte-for-byte for OCR comparison.
    """
    components: list[VisualComponent] = []
    for paragraph in paragraphs:
        text = str(paragraph or "").strip()
        if not text:
            continue
        step = _STEP_PREFIX.match(text) or _ORDINAL_PREFIX.match(text)
        if step:
            rest = step.group(2)
            metric = NUMBER_TOKEN.search(rest) if len(text) <= 120 else None
            components.append(VisualComponent(
                kind="step", text=text, label=step.group(1),
                value=metric.group(1) if metric else "",
            ))
            continue
        if text.startswith(("“", "「", "『", '"', "‘")):
            components.append(VisualComponent(kind="quote", text=text))
            continue
        number = NUMBER_TOKEN.search(text)
        if number and len(text) <= 120:
            components.append(VisualComponent(
                kind="stat", text=text, value=number.group(1),
                label=text[:number.start()] + text[number.end():],
            ))
            continue
        components.append(VisualComponent(kind="text", text=text))
    return components
