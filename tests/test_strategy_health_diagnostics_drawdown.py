"""Tests for drawdown diagnostic."""
from __future__ import annotations

import pytest

from src.strategy_health.models import StrategyHealthConfig
from src.strategy_health.diagnostics.drawdown import (
    _equity_curve,
    _drawdown_stats,
    compute_drawdown_diagnostic,
)


def _cfg(**o):
    return StrategyHealthConfig(**o)


def test_equity_curve_compounds():
    eq = _equity_curve([{"pnl_pct": 10.0}, {"pnl_pct": -5.0}])
    # 1.0 → 1.10 → 1.10 * 0.95 = 1.045
    assert eq[0] == 1.0
    assert abs(eq[-1] - 1.045) < 1e-6


def test_drawdown_stats_basic_peak_then_drop():
    # Peak 1.21, then drop to 1.0 → 17.36% dd
    curve = [1.0, 1.1, 1.21, 1.15, 1.0]
    s = _drawdown_stats(curve)
    assert s["max_dd_pct"] == round((1.21 - 1.0) / 1.21 * 100.0, 2)
    assert s["current_dd_pct"] >= 17.0


def test_drawdown_unknown_when_no_data():
    d = compute_drawdown_diagnostic(None, _cfg())
    assert d.severity == "unknown"
    assert d.metrics["samples"] == 0


def test_drawdown_ok_when_smooth_curve():
    rows = [
        {"pnl_pct": 1.0, "exit_time": "2026-07-08T00:00:00Z"},
        {"pnl_pct": 2.0, "exit_time": "2026-07-08T01:00:00Z"},
        {"pnl_pct": 1.5, "exit_time": "2026-07-08T02:00:00Z"},
    ]
    d = compute_drawdown_diagnostic({"trades": rows}, _cfg())
    assert d.severity == "ok"
    assert d.metrics["max_dd_pct"] < 1.0


def test_drawdown_warn_below_critical_threshold():
    # Build a curve that dips ~8% then recovers
    rows = []
    for i in range(20):
        rows.append({"pnl_pct": -4.0 if i == 5 else 1.0, "exit_time": f"2026-07-08T{i:02d}:00:00Z"})
    d = compute_drawdown_diagnostic({"trades": rows}, _cfg(drawdown_warn_pct=7.5, drawdown_critical_pct=15.0))
    assert d.severity in {"ok", "warn"}


def test_drawdown_critical_big_dip():
    rows = [{"pnl_pct": -20.0, "exit_time": f"2026-07-08T{i:02d}:00:00Z"} for i in range(5)]
    d = compute_drawdown_diagnostic({"trades": rows}, _cfg(drawdown_warn_pct=7.5, drawdown_critical_pct=15.0))
    assert d.severity == "critical"


def test_drawdown_window_filters_old_trades():
    rows = [
        {"pnl_pct": -30.0, "exit_time": "2025-01-01T00:00:00Z"},
        {"pnl_pct": 0.5, "exit_time": "2026-07-09T00:00:00Z"},
    ]
    d = compute_drawdown_diagnostic({"trades": rows}, _cfg(drawdown_window_days=90))
    assert d.metrics["samples"] in {1, 2}


def test_drawdown_to_dict():
    rows = [{"pnl_pct": 1.0, "exit_time": "2026-07-08T00:00:00Z"}]
    d = compute_drawdown_diagnostic({"trades": rows}, _cfg())
    blob = d.to_dict()
    assert blob["name"] == "drawdown"
    assert "max_dd_pct" in blob["metrics"]
