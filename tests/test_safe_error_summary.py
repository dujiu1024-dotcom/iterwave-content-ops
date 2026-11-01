from src.core.redact import safe_error_summary


def test_safe_error_summary_preserves_short_user_facing_message():
    assert safe_error_summary("平台登录已过期，请重新登录") == "平台登录已过期，请重新登录"


def test_safe_error_summary_hides_traceback_and_internal_paths():
    assert safe_error_summary('Traceback (most recent call last):\nFile "/srv/iterwave/app.py", line 12') == "操作失败，请稍后重试"
    assert safe_error_summary("读取失败：/srv/iterwave/data/private.db") == "读取失败：服务器内部路径"


def test_safe_error_summary_hides_internal_environment_names():
    assert safe_error_summary("配置失败：OPENAI_API_KEY is missing") == "操作失败，请稍后重试"


def test_safe_error_summary_truncates_long_messages():
    result = safe_error_summary("x" * 500)
    assert len(result) == 240
