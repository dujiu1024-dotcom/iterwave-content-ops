import pytest

from src.core import visual_ocr
from src.core.visual_ocr import compare_expected_text, normalize_ocr_text, load_ocr_report


def test_ocr_normalization_ignores_punctuation_and_width():
    assert normalize_ocr_text("ＡＢＣ，测试 １２３%") == "abc测试123%"


def test_ocr_gate_accepts_exact_copy_with_spacing_noise():
    result = compare_expected_text("今天增长了25%，新增用户100人", "今天 增长了25%，新增用户100人")
    assert result["passed"] is True
    assert result["missing_numbers"] == []


def test_ocr_gate_rejects_missing_or_changed_numbers():
    result = compare_expected_text("转化率提升25%，达到100人", "转化率提升2%，达到10人")
    assert result["passed"] is False
    assert result["missing_numbers"]


def test_ocr_gate_rejects_missing_chinese_copy():
    result = compare_expected_text("必须完整呈现这段中文内容", "必须呈现中文")
    assert result["passed"] is False


def test_missing_ocr_report_is_not_a_pass(tmp_path):
    image = tmp_path / "card.png"
    image.write_bytes(b"image")
    report = load_ocr_report([image])
    assert report["status"] == "unavailable"
    assert report["gate"] == "not_passed"



def test_ocr_gate_tolerates_minor_substitutions_and_line_order_noise():
    result = compare_expected_text(
        "内容生产流程升级，支持自动拆解和批量生成",
        "批量生成 内容生产流程升级，支持自动拆解和",
    )
    assert result["passed"] is True
    assert result["recall"] >= 0.86


def test_ocr_gate_keeps_numeric_values_strict_when_text_is_noisy():
    result = compare_expected_text(
        "转化率提升25%，新增用户100人",
        "新增用户100人，转化率提升26%",
    )
    assert result["passed"] is False
    assert result["missing_numbers"] == ["25%"]


def test_ocr_gate_rejects_substantial_copy_loss():
    result = compare_expected_text(
        "这是一段需要完整呈现的中文内容，不能随意省略关键步骤",
        "这是一段中文内容",
    )
    assert result["passed"] is False


def test_ocr_gate_tolerates_small_character_recognition_loss():
    result = compare_expected_text("ABCDEFGHIJ", "ABCDEFGHI")
    assert result["recall"] == 0.9
    assert result["passed"] is True



def test_ocr_hard_gate_reports_quality_metrics(tmp_path, monkeypatch):
    image = tmp_path / "card.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(
        visual_ocr,
        "run_ocr",
        lambda *_args, **_kwargs: {"status": "available", "text": "完全不同"},
    )
    with pytest.raises(ValueError, match="识别指标=.*recall"):
        visual_ocr.validate_rendered_pages(
            [image], ["需要完整呈现的正文2026"], tmp_path / "report.json",
        )


def test_ocr_gate_rejects_extra_numeric_tokens_and_occurrences():
    result = compare_expected_text("增长25%，新增100人", "增长25%，新增100人，另有2026年")
    assert result["passed"] is False
    assert result["extra_numbers"] == ["2026年"]


def test_ocr_gate_preserves_decimal_percent_date_and_units():
    expected = "转化率提升2.5%，日期为2026-11-03，新增100人"
    assert compare_expected_text(expected, expected)["passed"] is True
    assert compare_expected_text(expected, "转化率提升25%，日期为2026-11-04，新增100人")["passed"] is False
    assert compare_expected_text("预算为3.5万元", "预算为3.5元")["passed"] is False


def test_ocr_gate_rejects_negation_moved_to_another_semantic_segment():
    expected = "这个方案不允许发布。普通说明内容。"
    recognized = "这个方案允许发布。普通说明内容不。"
    result = compare_expected_text(expected, recognized)
    assert result["passed"] is False
    assert result["negation_mismatches"]


def test_ocr_gate_rejects_text_over_120_percent_of_expected():
    result = compare_expected_text("核心结论只有十个字", "核心结论只有十个字以及大量额外说明文字")
    assert result["passed"] is False
    assert result["length_ratio"] > 1.2


def test_critical_text_requires_exact_normalized_match():
    assert compare_expected_text("核心结论必须准确", "核心结论必须准确", critical=True)["passed"] is True
    assert compare_expected_text("核心结论必须准确", "核心结论必须大致准确", critical=True)["passed"] is False


def test_ocr_gate_checks_numeric_occurrence_counts_and_equivalent_date_formats():
    assert visual_ocr._numeric_tokens("2026年11月3日") == visual_ocr._numeric_tokens("2026-11-03")
    assert compare_expected_text("共有两个数字25%和25%", "共有一个数字25%")["passed"] is False


def test_ocr_gate_preserves_negative_numeric_sign():
    assert compare_expected_text("下降-5%", "下降5%")["passed"] is False


def test_ocr_gate_distinguishes_thousands_separator_from_decimal_point():
    assert compare_expected_text("曝光量128,450次", "曝光量128.450次")["passed"] is False


def test_ocr_gate_rejects_unexpected_negation_inserted_into_copy():
    result = compare_expected_text("这个方案允许发布。", "这个方案不允许发布。")
    assert result["passed"] is False
    assert result["negation_mismatches"]
