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
    assert result["missing_numbers"] == ["25"]


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
