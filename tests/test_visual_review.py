from src.core.visual_review import assess_visual_pages, parse_visual_review


def test_visual_review_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ITERWAVE_ENABLE_VISUAL_REVIEW", raising=False)
    result = assess_visual_pages([])
    assert result["status"] == "not_evaluated"
    assert result["score"] is None


def test_visual_review_requires_six_valid_dimensions():
    dimensions = {key: 80 for key in (
        "composition", "typography", "color", "hierarchy", "relevance", "polish"
    )}
    payload = {"choices": [{"message": {"content": __import__("json").dumps(
        {"dimensions": dimensions, "feedback": ["spacing"]}
    )}}]}
    result = parse_visual_review(payload)
    assert result["status"] == "evaluated"
    assert result["score"] == 80
    assert len(result["dimensions"]) == 6


def test_visual_review_rejects_partial_or_invalid_scores():
    payload = {"choices": [{"message": {"content": '{"dimensions":{"composition":120}}'}}]}
    result = parse_visual_review(payload)
    assert result["status"] == "not_evaluated"
    assert result["score"] is None
