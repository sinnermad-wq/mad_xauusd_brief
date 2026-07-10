"""Tests for regime diagnostic (Strategy Health v1)."""
from __future__ import annotations

import pytest

from src.strategy_health.models import StrategyHealthConfig
from src.strategy_health.diagnostics.regime import (
    _bucket_stats,
    _max_mix_shift,
    _mix_proportions,
    _session_stats,
    compute_regime_diagnostic,
)


def _cfg(**o) -> StrategyHealthConfig:
    return StrategyHealthConfig(**o)


# ---- helpers ----

def test_mix_proportions_basic():
    rec = [
        {"bias_regime": "trend"}, {"bias_regime": "trend"}, {"bias_regime": "range"},
    ]
    out = _mix_proportions(rec)
    assert out == {"trend": 0.6667, "range": 0.3333}


def test_mix_proportions_empty():
    assert _mix_proportions([]) == {}


def test_max_mix_shift_zero_when_identical():
    a = {"trend": 0.5, "range": 0.5}
    assert _max_mix_shift(a, dict(a)) == 0.0


def test_max_mix_shift_difference():
    a = {"trend": 1.0}
    b = {"range": 1.0}
    assert _max_mix_shift(a, b) == pytest.approx(1.0, abs=1e-6)


def test_bucket_stats_hit_rate():
    sigs = [
        {"bias_regime": "trend", "session_label": "london", "pnl_pct": 1.0},
        {"bias_regime": "trend", "session_label": "london", "pnl_pct": -0.5},
        {"bias_regime": "range", "session_label": "ny", "pnl_pct": 0.3},
    ]
    bs = _bucket_stats(sigs)
    assert bs["trend"]["n"] == 2
    assert bs["trend"]["hit_rate_pct"] == 50.0
    assert bs["range"]["n"] == 1
    assert bs["range"]["hit_rate_pct"] == 100.0


def test_session_stats_handles_missing_pnl():
    sigs = [
        {"bias_regime": "trend", "session_label": "london", "pnl_pct": None},
        {"bias_regime": "trend", "session_label": "london", "pnl_pct": 0.5},
    ]
    ss = _session_stats(sigs)
    assert ss["london"]["n"] == 2
    assert ss["london"]["wins"] == 1


# ---- diagnostic ----

def test_regime_unknown_when_no_signals():
    d = compute_regime_diagnostic(None, _cfg())
    assert d.severity == "unknown"
    assert d.metrics["samples"] == 0
    assert "missing_fusion_history" in d.reasons


def test_regime_ok_with_stable_mix():
    sigs = [
        {"bias_regime": "trend", "session_label": "ny", "pnl_pct": 0.4 if i % 2 else -0.3}
        for i in range(30)
    ]
    d = compute_regime_diagnostic(sigs, _cfg(regime_window=30))
    assert d.severity in {"ok", "warn"}
    assert d.metrics["samples"] == 30


def test_regime_critical_low_session():
    sigs = [{"bias_regime": "trend", "session_label": "asia", "pnl_pct": -0.5}] * 12 + \
           [{"bias_regime": "range", "session_label": "ny", "pnl_pct": 1.0}] * 18
    d = compute_regime_diagnostic(sigs, _cfg(regime_window=40))
    assert "asia" in d.metrics["disabled_sessions_candidates"]
    assert "low_hit_rate_sessions" in d.reasons


def test_regime_warn_mix_shift():
    sigs = (
        [{"bias_regime": "trend", "session_label": "ny", "pnl_pct": 0.1}] * 15 +
        [{"bias_regime": "range", "session_label": "asia", "pnl_pct": 0.1}] * 15
    )
    d = compute_regime_diagnostic(sigs, _cfg(regime_window=30))
    assert d.metrics["regime_mix_shift_pct"] >= 40.0
    assert "regime_mix_shift" in d.reasons


def test_regime_handles_garbage_records():
    """Garbage records are dropped; remaining ones drive metrics.

    A single valid signal cannot reasonably establish regime mix, so we
    only require the diagnostic not blow up and severity stays bounded
    in {ok, warn, unknown}.
    """
    sigs = [
        {"bias_regime": None, "session_label": None, "pnl_pct": "bogus"},
        None,
        "not-a-dict",
        {"bias_regime": "trend", "session_label": "london", "pnl_pct": 0.5},
    ]
    d = compute_regime_diagnostic(sigs, _cfg(regime_window=30))
    assert d.severity in {"ok", "warn", "unknown"}
    assert d.metrics["samples"] >= 1


def test_regime_to_dict_round_trip():
    sigs = [{"bias_regime": "trend", "session_label": "ny", "pnl_pct": 0.1}] * 5
    d = compute_regime_diagnostic(sigs, _cfg())
    blob = d.to_dict()
    assert blob["name"] == "regime"
    assert "regime_breakdown" in blob["metrics"]
