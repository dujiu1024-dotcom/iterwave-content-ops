from pathlib import Path

from src.core.visual_generation import generate_visual_background, revise_visual_background


def test_ai_visual_background_is_disabled_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ITERWAVE_ENABLE_AI_VISUALS", raising=False)
    result = generate_visual_background(
        title="测试标题", topic="测试主题", output_dir=tmp_path,
    )
    assert result["status"] == "disabled"
    assert result["path"] is None
    assert list(tmp_path.iterdir()) == []


def test_ai_visual_background_skips_when_key_is_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ITERWAVE_ENABLE_AI_VISUALS", "1")
    monkeypatch.setenv("ITERWAVE_IMAGE_API_KEY_ENV", "ITERWAVE_TEST_IMAGE_KEY")
    monkeypatch.delenv("ITERWAVE_TEST_IMAGE_KEY", raising=False)
    result = generate_visual_background(
        title="测试标题", topic="测试主题", output_dir=tmp_path,
    )
    assert result["status"] == "skipped_no_key"
    assert result["path"] is None


def test_visual_background_revision_is_disabled_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ITERWAVE_ENABLE_AI_VISUALS", raising=False)
    source = tmp_path / "source.png"
    source.write_bytes(b"not-read-when-disabled")
    result = revise_visual_background(
        source_path=source, feedback=["改善构图"], output_dir=tmp_path,
    )
    assert result["status"] == "disabled"
    assert result["path"] is None
    assert list(tmp_path.iterdir()) == [source]


def test_visual_background_revision_skips_without_key(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ITERWAVE_ENABLE_AI_VISUALS", "1")
    monkeypatch.setenv("ITERWAVE_IMAGE_API_KEY_ENV", "ITERWAVE_TEST_IMAGE_KEY")
    monkeypatch.delenv("ITERWAVE_TEST_IMAGE_KEY", raising=False)
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    result = revise_visual_background(
        source_path=source, feedback=[], output_dir=tmp_path,
    )
    assert result["status"] == "skipped_no_key"
