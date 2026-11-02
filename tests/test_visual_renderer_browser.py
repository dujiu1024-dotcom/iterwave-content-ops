from pathlib import Path

import pytest

from src.adapters.base import Platform
from src.core.visual_renderer import render_card_set


def test_chromium_renderer_produces_cover_and_body_pages(tmp_path: Path):
    paths = render_card_set(
        title="内容生产升级",
        subtitle="真实副标题",
        body="第一段内容\n第二段内容",
        output_dir=tmp_path,
        platform=Platform.XHS,
        max_pages=4,
    )
    assert len(paths) == 2
    assert all(path.is_file() and path.stat().st_size > 1000 for path in paths)
