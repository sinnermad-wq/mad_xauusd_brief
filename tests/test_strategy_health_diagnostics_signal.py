"""Tests for signal drift diagnostic."""
from __future__ import annotations

import pytest

from src.strategy_health.models import StrategyHealthConfig
from src.strategy_health.diagnostics.signal import (
    _coerce,
    _hit_rate,
    compute_signal_drift_diagnostic,
)


def _cfg(**o):
    return StrategyHealthConfig(**o)


def test_coerce_handles_string_and_missing():
    assert _coerce({"confidence": "0.4", "pnl_pct": "0.6"}) == {
        "confidence": 0.4, "pnl_pct": 0.6, "decision": "none"
    }
    assert _coerce({"pnl_pct": 1.0, "decision": "long"}) == {
        "confidence": 0.5, "pnl_pct": 1.0, "decision": "long"
    }
    assert _coerce({}) is None
    assert _coerce({"pnl_pct": "bad"}) is None


def test_hit_rate_empty():
    assert _hit_rate([]) == (0.0, 0)


def test_hit_rate_basic():
    rows = [{"pnl_pct": 1.0}, {"pnl_pct": -0.5}, {"pnl_pct": 0.0}, {"pnl_pct": 0.2}]
    hr, n = _hit_rate(rows)
    assert n == 4
    assert hr == 50.0


def test_signal_unknown_when_no_history():
    d = compute_signal_drift_diagnostic(None, _cfg())
    assert d.severity == "unknown"
    assert "missing_fusion_history" in d.reasons


def test_signal_ok_no_drift():
    rows = [
        {"confidence": 0.5 if i % 2 else 0.7, "pnl_pct": 0.5 if i % 2 else -0.2}
        for i in range(40)
    ]
    d = compute_signal_drift_diagnostic(rows, _cfg(signal_window=40))
    assert d.severity in {"ok", "warn"}


def test_signal_warn_low_conf_drift():
    # recent low-conf loses a lot more than prior low-conf
    base = [{"confidence": 0.4, "pnl_pct": 0.3} for _ in range(10)]
    recent = [{"confidence": 0.4, "pnl_pct": -0.4} for _ in range(20)]
    rows = base + recent
    d = compute_signal_drift_diagnostic(rows, _cfg(signal_window=30))
    assert d.severity in {"warn", "critical"}


def test_signal_critical_overall_collapse():
    base = [{"confidence": 0.7, "pnl_pct": 0.5} for _ in range(20)]
    recent = [{"confidence": 0.7, "pnl_pct": -0.5} for _ in range(20)]
    rows = base + recent
    d = compute_signal_drift_diagnostic(rows, _cfg(signal_window=40))
    assert d.severity == "critical"


def test_signal_to_dict():
    rows = [{"confidence": 0.6, "pnl_pct": 0.3}] * 20
    d = compute_signal_drift_diagnostic(rows, _cfg())
    blob = d.to_dict()
    assert blob["name"] == "signal"
    assert "low_confidence_threshold" in blob["metrics"]
