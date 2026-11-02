"""Single source of truth for selectable visual styles, fonts and component styles.

The catalog is data (``visual_catalog.json``), not code. The renderer, the round
executor, the manual regeneration API and the web UI (via ``GET /api/visual/catalog``)
all read it from here, so no layer keeps its own copy of the option lists.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).with_name("visual_catalog.json")
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
# profile key -> palette key
COLOR_FIELDS = (
    ("primary_color", "accent"),
    ("secondary_color", "muted"),
    ("background_color", "bg"),
    ("text_color", "fg"),
)
PROFILE_FIELDS = frozenset({
    "style_key", "brand_name", "primary_color", "secondary_color", "background_color",
    "text_color", "component_style", "font_family", "footer", "forbidden_styles",
})


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    if not data.get("styles") or not data.get("fonts") or not data.get("component_styles"):
        raise RuntimeError("visual_catalog.json 缺少 styles/fonts/component_styles")
    return data


def default_style() -> str:
    return load_catalog()["default_style"]


def default_font() -> str:
    return load_catalog()["default_font"]


def default_component_style() -> str:
    return load_catalog()["default_component_style"]


def style_keys() -> list[str]:
    return [item["key"] for item in load_catalog()["styles"]]


def font_families() -> list[str]:
    return [item["family"] for item in load_catalog()["fonts"]]


def component_style_keys() -> list[str]:
    return [item["key"] for item in load_catalog()["component_styles"]]


def style_palette(style_key: str) -> dict[str, str]:
    for item in load_catalog()["styles"]:
        if item["key"] == style_key:
            return dict(item["palette"])
    raise ValueError(f"未知视觉风格：{style_key}")


def style_is_dark(style_key: str) -> bool:
    for item in load_catalog()["styles"]:
        if item["key"] == style_key:
            return bool(item.get("dark"))
    raise ValueError(f"未知视觉风格：{style_key}")


def accepted_font_matches(font_family: str) -> set[str]:
    for item in load_catalog()["fonts"]:
        if item["family"] == font_family:
            return {name.casefold() for name in item.get("accepted_matches") or [font_family]}
    raise ValueError(f"不支持的字体族：{font_family}")


def font_installed(font_family: str) -> tuple[bool, str]:
    """Return (installed, matched family) using fontconfig; never guesses a pass."""
    executable = shutil.which("fc-match")
    if not executable:
        return False, "fontconfig 未安装"
    try:
        result = subprocess.run(
            [executable, "-f", "%{family}", font_family],
            check=True, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "fc-match 执行失败"
    matched = {name.strip().casefold() for name in result.stdout.split(",") if name.strip()}
    return bool(matched & accepted_font_matches(font_family)), result.stdout.strip()


def public_catalog() -> dict[str, Any]:
    """Catalog as served to the UI, with host font availability."""
    data = load_catalog()
    fonts = []
    for item in data["fonts"]:
        installed, matched = font_installed(item["family"])
        fonts.append({"family": item["family"], "label": item["label"],
                      "installed": installed, "matched": matched})
    return {
        "version": data["version"],
        "default_style": data["default_style"],
        "default_font": data["default_font"],
        "default_component_style": data["default_component_style"],
        "styles": [
            {"key": s["key"], "label": s["label"], "note": s["note"],
             "palette": dict(s["palette"]), "dark": bool(s.get("dark"))}
            for s in data["styles"]
        ],
        "fonts": fonts,
        "component_styles": [dict(c) for c in data["component_styles"]],
    }


def validate_brand_profile(profile: dict) -> None:
    """Raise ValueError with a user-facing message when a stored/submitted profile is invalid."""
    unknown = set(profile) - PROFILE_FIELDS
    if unknown:
        raise ValueError(f"不支持的品牌配置字段：{', '.join(sorted(unknown))}")
    styles = set(style_keys())
    style_key = profile.get("style_key") or default_style()
    if style_key not in styles:
        raise ValueError("未知视觉风格")
    for field, _ in COLOR_FIELDS:
        value = profile.get(field)
        if value not in (None, "") and (not isinstance(value, str) or not HEX_COLOR.fullmatch(value)):
            raise ValueError(f"{field} 必须是 #RRGGBB 格式")
    for field in ("brand_name", "footer"):
        value = profile.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 120):
            raise ValueError(f"{field} 必须是 120 字以内文本")
    component_style = profile.get("component_style") or default_component_style()
    if component_style not in component_style_keys():
        raise ValueError(f"component_style 必须为 {'、'.join(component_style_keys())}")
    font = profile.get("font_family")
    if font not in (None, "") and font not in font_families():
        raise ValueError("字体必须选择系统支持的字体族")
    forbidden = profile.get("forbidden_styles", [])
    if forbidden is None:
        forbidden = []
    if not isinstance(forbidden, list) or len(forbidden) > 20 or any(
        not isinstance(item, str) or item not in styles for item in forbidden
    ):
        raise ValueError("forbidden_styles 必须是最多 20 个有效风格键")
    if style_key in forbidden:
        raise ValueError("默认风格不能同时列入禁用风格")


def resolve_render_options(profile: dict | None) -> dict[str, Any]:
    """Map a tenant brand profile to renderer kwargs; invalid stored values fail closed."""
    profile = profile if isinstance(profile, dict) else {}
    validate_brand_profile(profile)
    theme = profile.get("style_key") or default_style()
    palette: dict[str, str] = {}
    for field, key in COLOR_FIELDS:
        value = profile.get(field)
        if isinstance(value, str) and HEX_COLOR.fullmatch(value):
            palette[key] = value
    palette["component_style"] = profile.get("component_style") or default_component_style()
    return {
        "theme": theme,
        "brand_palette": palette,
        "font_family": profile.get("font_family") or default_font(),
    }


def brand_footer_text(profile: dict | None) -> str:
    profile = profile if isinstance(profile, dict) else {}
    return " · ".join(
        str(profile.get(key) or "").strip() for key in ("brand_name", "footer")
        if str(profile.get(key) or "").strip()
    )
