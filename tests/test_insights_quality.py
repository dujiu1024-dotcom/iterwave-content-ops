from __future__ import annotations

from datetime import datetime, timedelta

from src.core.insights import PlatformInsightsEngine


def _metric_for_content(conn, content_id: str, rate: float, *, fetched_at: str | None = None):
    draft_id = conn.execute(
        "SELECT id FROM drafts WHERE content_id=? ORDER BY id LIMIT 1", (content_id,),
    ).fetchone()[0]
    attempt = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) + 1 FROM metrics WHERE content_id=? AND platform='xhs'",
        (content_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO metrics (draft_id, content_id, platform, fetch_status, fetched_at, attempt, "
        "views, likes, collects, comments, interaction_rate, like_rate, collect_rate) "
        "VALUES (?, ?, 'xhs', 'success', ?, ?, 100, 10, 5, 2, ?, ?, ?)",
        (draft_id, content_id, fetched_at or datetime.now().isoformat(sep=" "),
         attempt, rate, rate / 2, rate / 4),
    )


def test_insights_are_scoped_to_requested_round(cfg, conn, seed):
    first_round = seed.round()
    second_round = seed.round()
    first_draft = seed.draft(first_round, views=100, likes=10, collects=5)
    second_draft = seed.draft(second_round, views=100, likes=20, collects=5)
    first_content = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (first_draft,),
    ).fetchone()[0]
    second_content = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (second_draft,),
    ).fetchone()[0]
    _metric_for_content(conn, first_content, 0.10)
    _metric_for_content(conn, second_content, 0.20)
    conn.commit()

    engine = PlatformInsightsEngine(cfg, conn)
    result = engine._analyze_one("xhs", first_round, 365)

    assert result.sample_count == 1
    assert result.avg_interaction_rate == 0.10


def test_insights_count_one_content_once_despite_retry_metrics(cfg, conn, seed):
    round_id = seed.round()
    draft_id = seed.draft(round_id, views=100, likes=10, collects=5)
    content_id = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (draft_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE drafts SET user_confirmed_at='2026-11-01T10:00:00' WHERE id=?",
        (draft_id,),
    )
    _metric_for_content(conn, content_id, 0.10)
    _metric_for_content(conn, content_id, 0.30)
    conn.commit()

    result = PlatformInsightsEngine(cfg, conn)._analyze_one("xhs", round_id, 365)

    assert result.sample_count == 1
    assert result.avg_interaction_rate == 0.30


def test_null_rate_groups_sort_last_and_are_not_selected_as_best(cfg):
    engine = PlatformInsightsEngine(cfg, object())
    samples = [
        {
            "dimension": "缺失指标", "interaction_rate": None,
            "title": "无指标标题" * 6, "card_style": "无指标风格",
        },
        {
            "dimension": "有效指标", "interaction_rate": 0.0,
            "title": "短标题", "card_style": "有效风格",
        },
    ]

    dimensions = engine._group_by_dimension(samples)
    title_style = engine._title_style(samples)
    card_style = engine._best_group(samples, key="card_style", label_field="card_style")

    assert dimensions[0]["dimension"] == "有效指标"
    assert dimensions[-1]["dimension"] == "缺失指标"
    assert title_style["best_bucket"] == "0-12"
    assert card_style["card_style"] == "有效风格"


def test_ensure_fresh_does_not_reuse_another_round_cache(cfg, conn, seed):
    first_round = seed.round()
    second_round = seed.round()
    first_draft = seed.draft(first_round, views=100, likes=10, collects=5)
    second_draft = seed.draft(second_round, views=100, likes=20, collects=5)
    first_content = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (first_draft,),
    ).fetchone()[0]
    second_content = conn.execute(
        "SELECT content_id FROM drafts WHERE id=?", (second_draft,),
    ).fetchone()[0]
    _metric_for_content(conn, first_content, 0.10)
    _metric_for_content(conn, second_content, 0.20)
    conn.commit()

    engine = PlatformInsightsEngine(cfg, conn)
    engine.analyze(round_id=first_round, days=365)
    rows = engine.ensure_fresh(round_id=second_round, days=365, max_age_minutes=10000)

    assert rows
    assert {row["round_id"] for row in rows} == {second_round}
    xhs = next(row for row in rows if row["platform"] == "xhs")
    assert xhs["sample_count"] == 1
    assert xhs["avg_interaction_rate"] == 0.20


def test_student_t_critical_value_uses_finite_df_correction_above_29():
    critical = PlatformInsightsEngine._student_t_critical_975(30)

    assert 2.041 < critical < 2.043
    assert 1.961 < PlatformInsightsEngine._student_t_critical_975(1000) < 1.963


def test_student_t_critical_value_rejects_invalid_degrees_of_freedom():
    import pytest

    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            PlatformInsightsEngine._student_t_critical_975(value)


def test_malformed_or_non_finite_interaction_rates_are_missing_not_zero(cfg, conn, seed):
    round_id = seed.round()
    draft_id = seed.draft(round_id, views=100, likes=10, collects=5)
    conn.execute(
        "UPDATE metrics SET interaction_rate='not-a-number' WHERE draft_id=?",
        (draft_id,),
    )
    conn.commit()

    result = PlatformInsightsEngine(cfg, conn)._analyze_one("xhs", round_id, 365)

    assert result.sample_count == 0
    assert result.avg_interaction_rate is None
    assert result.interaction_rate_ci95 is None
