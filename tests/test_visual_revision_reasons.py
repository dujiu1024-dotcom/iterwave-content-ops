from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from src.core.visual_generation import revise_visual_background


def _png(path: Path, size=(32, 32), color=(20, 80, 140)):
    image = Image.new("RGB", size, color)
    image.save(path, format="PNG")
    return path


def test_single_page_revision_requires_a_user_reason(tmp_path):
    source = _png(tmp_path / "target.png")
    with pytest.raises(ValueError, match="必须提供用户修改理由"):
        revise_visual_background(
            source_path=source, feedback=[], output_dir=tmp_path / "out",
            settings={"model": "test-image-model"},
        )


def test_single_page_revision_uses_neighboring_visual_references_and_persists_prompt(tmp_path, monkeypatch):
    source = _png(tmp_path / "target.png", color=(10, 20, 30))
    neighbor = _png(tmp_path / "neighbor.png", color=(30, 40, 50))
    captured = {}
    output = BytesIO()
    Image.new("RGB", (32, 32), (60, 70, 80)).save(output, format="PNG")

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["source_path"] = kwargs.get("source_path")
        captured["source_mime_type"] = kwargs.get("source_mime_type")
        assert Path(captured["source_path"]).is_file()
        return output.getvalue(), "image/png", ".png", 32, 32

    monkeypatch.setattr("src.core.media_generation.generate_image_bytes", fake_generate)
    audit = {}
    result = revise_visual_background(
        source_path=source,
        feedback=["把背景改为浅色，保留产品主体"],
        output_dir=tmp_path / "out",
        settings={"model": "test-image-model", "size": "1024x1536"},
        style_baseline={"style_key": "brand-clean", "primary_color": "#123456"},
        neighbor_context=[{"page_index": 1, "body_summary": "上一页的风格说明"}],
        reference_paths=[neighbor],
        preserve_items=["产品主体和全部文字"],
        forbidden_items=["不得修改其他页面"],
        prompt_audit=audit,
    )

    assert result["status"] == "revised"
    assert Path(result["path"]).is_file()
    assert "把背景改为浅色，保留产品主体" in captured["prompt"]
    assert "brand-clean" in captured["prompt"]
    assert "上一页的风格说明" in captured["prompt"]
    assert "不得修改其他页面" in captured["prompt"]
    assert captured["source_mime_type"] == "image/png"
    assert Path(captured["source_path"]) != source
    assert not Path(captured["source_path"]).exists()
    assert audit["final_prompt"] == result["final_prompt"]
    assert str(neighbor) in audit["reference_images"]
