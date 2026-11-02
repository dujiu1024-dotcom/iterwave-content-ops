from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.adapters.base import Platform
from src.core import timezone
from src.core.metrics import Normalizer


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, values):
        self.values = values
        self.query = None

    def execute(self, sql, params):
        self.query = (sql, params)
        return _Cursor([(value,) for value in self.values])


def _cfg():
    return SimpleNamespace(
        business_timezone="Asia/Shanghai",
        scoring=SimpleNamespace(normalization_window_days=90),
    )


def test_normalizer_uses_business_timezone_for_history_window(monkeypatch):
    cfg = _cfg()
    observed = {}

    def fixed_now(received_cfg):
        observed["cfg"] = received_cfg
        return datetime(2026, 11, 2, 12, 0, 0)

    monkeypatch.setattr(timezone, "business_now", fixed_now)
    conn = _Connection([1.0, 2.0, 3.0])
    stats = Normalizer(conn, cfg).compute_platform_stats(Platform.XHS, "views")

    assert stats is not None
    assert observed["cfg"] is cfg
    assert conn.query[1] == ("xhs", "2026-08-04T12:00:00")


def test_normalizer_winsorizes_extreme_values_for_large_samples(monkeypatch):
    monkeypatch.setattr(timezone, "business_now", lambda _cfg: datetime(2026, 11, 2))
    values = [float(value) for value in range(1, 100)] + [10000.0]
    stats = Normalizer(_Connection(values), _cfg()).compute_platform_stats(
        Platform.XHS, "views",
    )

    assert stats is not None
    assert stats.sample_count == 100
    assert stats.mean < 60.0


def test_normalizer_does_not_winsorize_small_samples(monkeypatch):
    monkeypatch.setattr(timezone, "business_now", lambda _cfg: datetime(2026, 11, 2))
    values = [1.0, 2.0, 3.0, 1000.0]
    stats = Normalizer(_Connection(values), _cfg()).compute_platform_stats(
        Platform.XHS, "views",
    )

    assert stats is not None
    assert stats.mean == 251.5
