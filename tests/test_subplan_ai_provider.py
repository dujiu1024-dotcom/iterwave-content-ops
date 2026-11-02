from src.api.subplans import _parse_subplan_model_content
from src.core.config import LlmConfig


def test_subplan_model_response_parser_accepts_wrapped_json():
    parsed = _parse_subplan_model_content(
        'Here is the plan: {"subplans":[{"platform":"xhs","stages":[]}]}'
    )
    assert parsed["subplans"][0]["platform"] == "xhs"


def test_subplan_model_response_parser_accepts_multimodal_text_parts():
    parsed = _parse_subplan_model_content([
        {"type": "text", "text": '{"subplans":['},
        {"type": "text", "text": '{"platform":"xhs"}]}'},
    ])
    assert parsed["subplans"] == [{"platform": "xhs"}]


def test_subplan_model_response_parser_rejects_invalid_payloads():
    import pytest

    with pytest.raises(ValueError, match="缺少 subplans"):
        _parse_subplan_model_content('{"items":[]}')
    with pytest.raises(ValueError, match="无效"):
        _parse_subplan_model_content('{"subplans":[')
    with pytest.raises(ValueError, match="缺少文本"):
        _parse_subplan_model_content(None)


def test_llm_fallback_channel_configuration_is_independently_overridable():
    config = LlmConfig(
        fallback_provider="secondary",
        fallback_base_url="https://secondary.example/v1",
        fallback_api_key_env="SECONDARY_API_KEY",
    )
    assert config.fallback_provider == "secondary"
    assert config.fallback_base_url == "https://secondary.example/v1"
    assert config.fallback_api_key_env == "SECONDARY_API_KEY"
