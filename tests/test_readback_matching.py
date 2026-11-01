"""Regression tests for safe platform work matching."""

from src.adapters.readback import match_work


def test_match_work_prefers_exact_platform_content_id():
    works = [
        {"content_id": "id-1", "title": "相同标题"},
        {"content_id": "id-2", "title": "相同标题"},
    ]
    assert match_work(works, "相同标题", "id-2") == works[1]


def test_match_work_accepts_unique_normalized_exact_title():
    work = {"content_id": "id-1", "title": "ＡＩ  教育：入门指南"}
    assert match_work([work], "AI 教育：入门指南") == work


def test_match_work_accepts_unique_long_prefix_when_platform_adds_suffix():
    work = {"content_id": "id-1", "title": "这是一篇足够长的测试标题用于验证发布确认匹配逻辑（平台附加）"}
    assert match_work([work], "这是一篇足够长的测试标题用于验证发布确认匹配逻辑") == work


def test_match_work_rejects_ambiguous_long_prefix():
    prefix = "这是一篇足够长的测试标题用于验证发布确认匹配逻辑"
    works = [
        {"content_id": "id-1", "title": prefix + " A"},
        {"content_id": "id-2", "title": prefix + " B"},
    ]
    assert match_work(works, prefix) is None


def test_match_work_rejects_arbitrary_substring_match():
    work = {"content_id": "id-1", "title": "其他作品标题包含测试标题的一部分但不是同一篇"}
    assert match_work([work], "测试标题的一部分") is None
