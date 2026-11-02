from __future__ import annotations

import pytest

from src.core.insights import PlatformInsightsEngine


def test_mean_confidence_interval_uses_student_t_for_small_samples():
    interval = PlatformInsightsEngine._mean_ci95([0.10, 0.20, 0.30])

    assert interval is not None
    assert interval[0] < 0.20 < interval[1]
    # df=2 的 95% 双侧 t 临界值为 4.303，区间应明显宽于正态近似。
    assert interval[0] == pytest.approx(0.20 - 4.303 * 0.10 / (2 ** 0.5), abs=1e-6)


def test_mean_confidence_interval_is_missing_for_single_sample():
    assert PlatformInsightsEngine._mean_ci95([0.12]) is None


def test_mean_confidence_interval_collapses_for_constant_values():
    interval = PlatformInsightsEngine._mean_ci95([0.2, 0.2, 0.2])
    assert interval is not None
    assert interval[0] == pytest.approx(0.2)
    assert interval[1] == pytest.approx(0.2)
