"""Versioned, centralized visual output specifications for supported platforms."""
from __future__ import annotations

from dataclasses import dataclass

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
