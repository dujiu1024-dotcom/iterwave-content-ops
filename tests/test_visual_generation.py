import sqlite3
from pathlib import Path

from src.core import visual_generation
from src.core.content_pipeline import image_generation_settings
from src.core.visual_generation import revise_visual_background


def test_visual_background_revision_is_disabled_without_media_settings(tmp_path: Path):
    source = tmp_path / "source.png"
    source.write_bytes(b"not-read-when-disabled")
    result = revise_visual_background(
        source_path=source, feedback=["改善构图"], output_dir=tmp_path, settings=None,
    )
    assert result["status"] == "disabled"
    assert result["path"] is None
    assert list(tmp_path.iterdir()) == [source]


def test_env_switch_no_longer_enables_ai_visuals(monkeypatch, tmp_path: Path):
    """Config source is media_generation_settings only; the old env flag is ignored."""
    monkeypatch.setenv("ITERWAVE_ENABLE_AI_VISUALS", "1")
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    result = revise_visual_background(
        source_path=source, feedback=[], output_dir=tmp_path, settings=None,
    )
    assert result["status"] == "disabled"


def test_revision_uses_media_generation_settings_model_and_prefix(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_generate(prompt, *, model, size, source_path=None, source_mime_type=None, cfg=None, **_):
        calls.update(prompt=prompt, model=model, size=size, source=source_path)
        return b"\x89PNG-fake", "image/png", ".png", 1024, 1536

    monkeypatch.setattr("src.core.media_generation.generate_image_bytes", fake_generate)
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    result = revise_visual_background(
        source_path=source, feedback=["更明亮"], output_dir=tmp_path,
        settings={"prefix": "品牌风格前缀", "model": "img-model-x", "size": "1024x1536"},
    )
    assert result["status"] == "revised"
    assert calls["model"] == "img-model-x"
    assert calls["prompt"].startswith("品牌风格前缀")
    assert Path(result["path"]).is_file()


def test_image_generation_settings_reads_single_db_source():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert image_generation_settings(conn)[0] is None  # table missing -> disabled
    conn.execute("CREATE TABLE media_generation_settings (id INTEGER PRIMARY KEY, enabled INTEGER, "
                 "prompt_prefix TEXT, model TEXT, size TEXT)")
    conn.execute("INSERT INTO media_generation_settings VALUES (1, 0, '', 'm', '1024x1536')")
    assert image_generation_settings(conn)[0] is None
    conn.execute("UPDATE media_generation_settings SET enabled=1")
    settings, reason = image_generation_settings(conn)
    assert settings == {"prefix": "", "model": "m", "size": "1024x1536"} and reason == ""


def test_module_has_no_env_based_generation_entrypoint():
    assert not hasattr(visual_generation, "generate_visual_background")
