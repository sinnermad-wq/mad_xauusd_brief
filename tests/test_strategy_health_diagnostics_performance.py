"""Tests for performance diagnostic (Strategy Health v1)."""
from __future__ import annotations

import pytest

from src.strategy_health.models import StrategyHealthConfig
from src.strategy_health.diagnostics.performance import (
    _extract_trades,
    _rolling_window,
    _summarise,
    _trade_pnl,
    compute_performance_diagnostic,
)


def _cfg(**overrides) -> StrategyHealthConfig:
    return StrategyHealthConfig(**overrides)


def test_extract_trades_accepts_top_level():
    assert _extract_trades({"trades": [{"pnl_pct": 1.0}]}) == [{"pnl_pct": 1.0}]


def test_extract_trades_accepts_outcomes():
    r = _extract_trades({"outcomes": [{"pnl_pct": 0.5}]})
    assert r == [{"pnl_pct": 0.5}]


def test_extract_trades_accepts_stats_nested():
    r = _extract_trades({"stats": {"trades": [{"pnl_pct": 0.5}]}})
    assert r == [{"pnl_pct": 0.5}]


def test_extract_trades_handles_missing():
    assert _extract_trades(None) == []
    assert _extract_trades({}) == []
    assert _extract_trades({"foo": "bar"}) == []


def test_trade_pnl_handles_string_and_missing():
    assert _trade_pnl({"pnl_pct": "1.5"}) == 1.5
    assert _trade_pnl({"pnl": 2.0}) == 2.0
    assert _trade_pnl({"return_pct": "bad"}) is None
    assert _trade_pnl({}) is None


def test_rolling_window_size_and_zero():
    t = [{"pnl_pct": i} for i in range(5)]
    assert len(_rolling_window(t, 3)) == 3
    assert len(_rolling_window(t, 0)) == 0
    assert len(_rolling_window([], 5)) == 0


def test_summarise_hit_rate_and_expectancy():
    sigs = [{"pnl_pct": v} for v in [1.0, 1.0, -0.5]]
    s = _summarise(sigs)
    assert s["n"] == 3
    assert s["hit_rate"] == pytest.approx(66.6667, abs=1e-2)
    assert s["expectancy"] == pytest.approx(0.5, abs=1e-3)


def test_performance_unknown_when_missing():
    d = compute_performance_diagnostic(None, _cfg())
    assert d.severity == "unknown"
    assert d.metrics["samples"] == 0
    assert d.source == "backtest"


def test_performance_ok_within_baseline():
    trades = {"trades": [{"pnl_pct": v} for v in [1.0, -0.5, 0.7, 0.4, 0.9, -0.2, 0.6]]}
    d = compute_performance_diagnostic(trades, _cfg(performance_window=20))
    assert d.severity in {"ok", "warn"}
    assert d.metrics["samples"] == 7


def test_performance_warn_negative_expectancy():
    trades = {"trades": [{"pnl_pct": -0.1}] * 12 + [{"pnl_pct": 0.18}] * 8}
    d = compute_performance_diagnostic(trades, _cfg(performance_window=40))
    assert d.metrics["hit_rate_pct"] == 40.0
    assert d.severity in {"warn", "critical"}


def test_performance_critical_far_below_baseline():
    trades = {"trades": [{"pnl_pct": -1.0}] * 25}
    d = compute_performance_diagnostic(trades, _cfg(performance_window=20))
    assert d.severity == "critical"
    assert "hit_rate_far_below_baseline" in d.reasons


def test_performance_warn_insufficient_samples():
    trades = {"trades": [{"pnl_pct": v} for v in [1.0, 1.0, -0.5]]}
    d = compute_performance_diagnostic(trades, _cfg(performance_window=20))
    assert "insufficient_samples" in d.reasons


def test_performance_to_dict_round_trip():
    trades = {"trades": [{"pnl_pct": 1.0}] * 10}
    d = compute_performance_diagnostic(trades, _cfg())
    blob = d.to_dict()
    assert blob["name"] == "performance"
    assert blob["severity"] == d.severity
    assert isinstance(blob["metrics"], dict)
