from pathlib import Path
import sqlite3

import pytest

from src.core.visual_renderer import (
    THEMES,
    _document,
    _validate_theme_contrast,
    render_single_page,
)
from src.core.visual_persistence import create_visual_version
from src.core.visual_specs import PLATFORM_SPECS


def test_brand_palette_can_override_only_visual_accent_and_muted():
    palette = {**THEMES["knowledge_infographic"], "accent": "#123456", "muted": "#456789"}
    assert palette["accent"] == "#123456"
    assert palette["muted"] == "#456789"
    assert _validate_theme_contrast("knowledge_infographic", False, palette) >= 4.5


def test_renderer_rejects_unapproved_font_family():
    with pytest.raises(ValueError, match="不支持的字体族"):
        _document(
            title="标题", subtitle="副标题", paragraphs=[], footer="",
            page_text="1/2", theme="knowledge_infographic", cover=True,
            width=1080, height=1440, font_family='Arial";background:red;/*',
        )


def test_brand_profile_is_tenant_scoped(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migration = Path(__file__).parents[1] / "migrations" / "118_visual_brand_profiles.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO visual_brand_profiles(tenant_id, profile_json) VALUES(?, ?)",
        ("tenant-a", '{"brand_name":"A"}'),
    )
    conn.execute(
        "INSERT INTO visual_brand_profiles(tenant_id, profile_json) VALUES(?, ?)",
        ("tenant-b", '{"brand_name":"B"}'),
    )
    assert conn.execute(
        "SELECT profile_json FROM visual_brand_profiles WHERE tenant_id='tenant-a'"
    ).fetchone()[0] == '{"brand_name":"A"}'
    assert conn.execute(
        "SELECT profile_json FROM visual_brand_profiles WHERE tenant_id='tenant-b'"
    ).fetchone()[0] == '{"brand_name":"B"}'
    conn.close()


def test_single_page_renderer_rejects_out_of_range_page_before_io(tmp_path):
    with pytest.raises(ValueError, match="页面索引无效"):
        render_single_page(
            title="标题", subtitle="副标题", body="第一段\n第二段",
            page_index=4, total_pages=3, output_dir=tmp_path,
            platform=__import__("src.adapters.base", fromlist=["Platform"]).Platform.XHS,
            footer="", theme="knowledge_infographic",
            background_path=tmp_path / "not-created.png",
        )


def test_single_page_renderer_rejects_changed_pagination_before_io(tmp_path):
    with pytest.raises(ValueError, match="正文分页与历史版本不一致"):
        render_single_page(
            title="标题", subtitle="副标题", body="第一段\n第二段\n第三段\n第四段\n第五段\n第六段",
            page_index=1, total_pages=2, output_dir=tmp_path,
            platform=__import__("src.adapters.base", fromlist=["Platform"]).Platform.XHS,
            footer="", theme="knowledge_infographic",
            background_path=tmp_path / "not-created.png",
        )


def test_every_supported_platform_has_explicit_visual_spec():
    from src.adapters.base import Platform

    assert set(PLATFORM_SPECS) == {
        Platform.XHS, Platform.DOUYIN, Platform.KUAISHOU, Platform.WECHAT_CHANNELS,
    }
    for spec in PLATFORM_SPECS.values():
        assert spec.width > 0 and spec.height > 0
        assert spec.output_format == "png"
        assert spec.max_pages >= 2
