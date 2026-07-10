"""Tests for cost diagnostic."""
from __future__ import annotations

import pytest

from src.strategy_health.models import StrategyHealthConfig
from src.strategy_health.diagnostics.cost import (
    _coerce_trade,
    _window_trades,
    compute_cost_diagnostic,
)


def _cfg(**o):
    return StrategyHealthConfig(**o)


def test_coerce_trade_handles_string_pnl():
    t = _coerce_trade({"pnl_pct": "0.5", "cost_pct": "0.02"})
    assert t is not None
    assert t["pnl_pct"] == 0.5
    assert t["cost_pct"] == 0.02


def test_coerce_trade_drop_when_no_pnl():
    t = _coerce_trade({"cost_pct": 0.02})
    assert t is None


def test_window_trades_respects_size():
    rows = [{"pnl_pct": 0.5, "cost_pct": 0.02}] * 100
    assert len(_window_trades({"trades": rows}, 25)) == 25
    assert len(_window_trades({"trades": rows}, 0)) == 100


def test_cost_unknown_when_no_data():
    d = compute_cost_diagnostic(None, _cfg())
    assert d.severity == "unknown"
    assert "missing_cost_data" in d.reasons


def test_cost_ok_when_costs_low():
    rows = [{"pnl_pct": 1.0, "cost_pct": 0.01}] * 30
    d = compute_cost_diagnostic({"trades": rows}, _cfg(cost_window=50))
    assert "cost_within_acceptable_range" in d.reasons
    assert d.metrics["cost_drag_pct"] < 10.0


def test_cost_warn_when_drag_in_band():
    # Costs about 12% of total_pnl+cost.
    rows = [{"pnl_pct": 0.5, "cost_pct": 0.07}] * 50
    d = compute_cost_diagnostic({"trades": rows}, _cfg(cost_window=50))
    assert d.severity in {"warn", "critical"}
    assert d.metrics["samples"] == 50


def test_cost_critical_when_drag_huge():
    rows = [{"pnl_pct": 0.1, "cost_pct": 0.5}] * 50
    d = compute_cost_diagnostic({"trades": rows}, _cfg(cost_window=50))
    assert d.severity == "critical"


def test_cost_handles_negative_pnl():
    rows = [{"pnl_pct": -0.5, "cost_pct": 0.05}] * 30
    d = compute_cost_diagnostic({"trades": rows}, _cfg())
    assert d.metrics["samples"] == 30


def test_cost_metrics_round_trip():
    rows = [{"pnl_pct": 0.4, "cost_pct": 0.02}] * 20
    d = compute_cost_diagnostic({"trades": rows}, _cfg())
    blob = d.to_dict()
    assert blob["name"] == "cost"
    assert isinstance(blob["metrics"]["cost_drag_pct"], float)
