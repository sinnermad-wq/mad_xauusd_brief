"""Tests for suggestion engine."""
from __future__ import annotations

import pytest

from src.strategy_health.models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARN,
    SEVERITY_UNKNOWN,
    StrategyDiagnostic,
)
from src.strategy_health.suggestion import (
    _PRIORITY,
    compute_suggestions,
)
from src.strategy_health.approval import diff_approvals


def _diag(name, severity, metrics=None):
    return StrategyDiagnostic(
        name=name, severity=severity, summary="test",
        metrics=metrics or {}, reasons=(), source="test",
    )


def _cfg(**o):
    from src.strategy_health.models import StrategyHealthConfig
    return StrategyHealthConfig(**o)


def test_keep_running_when_all_ok():
    diags = [_diag("performance", SEVERITY_OK), _diag("regime", SEVERITY_OK),
             _diag("cost", SEVERITY_OK), _diag("signal", SEVERITY_OK),
             _diag("drawdown", SEVERITY_OK), _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg())
    assert len(sugs) == 1
    assert sugs[0].kind == "keep_running"


def test_watch_only_when_warn():
    diags = [_diag("performance", SEVERITY_WARN),
             _diag("regime", SEVERITY_OK), _diag("cost", SEVERITY_OK),
             _diag("signal", SEVERITY_OK), _diag("drawdown", SEVERITY_OK),
             _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg())
    assert sugs[0].kind == "watch_only"


def test_pause_strategy_when_2_criticals():
    diags = [_diag("performance", SEVERITY_CRITICAL),
             _diag("regime", SEVERITY_CRITICAL),
             _diag("cost", SEVERITY_OK), _diag("signal", SEVERITY_OK),
             _diag("drawdown", SEVERITY_OK), _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg())
    assert sugs[0].kind == "pause_strategy"


def test_disable_session_low_hit_rate():
    diags = [_diag("regime", SEVERITY_WARN,
                   {"disabled_sessions_candidates": ["asia"],
                    "session_breakdown": {"asia": {"hit_rate_pct": 20.0, "n": 15}},
                    "disable_threshold_pct": 35.0}),
             _diag("performance", SEVERITY_OK), _diag("cost", SEVERITY_OK),
             _diag("signal", SEVERITY_OK), _diag("drawdown", SEVERITY_OK),
             _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg())
    kinds = [s.kind for s in sugs]
    assert "disable_session" in kinds


def test_tighten_filter_low_conf_drift():
    diags = [_diag("signal", SEVERITY_WARN,
                   {"low_conf_recent_vs_prior_ratio": 0.4,
                    "recent_low_conf_hit_rate_pct": 30.0,
                    "prior_low_conf_hit_rate_pct": 65.0}),
             _diag("performance", SEVERITY_OK), _diag("regime", SEVERITY_OK),
             _diag("cost", SEVERITY_OK), _diag("drawdown", SEVERITY_OK),
             _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg(low_conf_drift_ratio_warn=0.65))
    kinds = [s.kind for s in sugs]
    assert "tighten_filter" in kinds


def test_reduce_size_drawdown_warn():
    diags = [_diag("drawdown", SEVERITY_WARN,
                   {"max_dd_pct": 10.0, "current_dd_pct": 5.0}),
             _diag("performance", SEVERITY_OK), _diag("regime", SEVERITY_OK),
             _diag("cost", SEVERITY_OK), _diag("signal", SEVERITY_OK),
             _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg(drawdown_warn_pct=7.5))
    kinds = [s.kind for s in sugs]
    assert "reduce_size" in kinds


def test_review_parameters_regime_shift():
    diags = [_diag("regime", SEVERITY_WARN,
                   {"regime_mix_shift_pct": 55.0,
                    "regime_mix_shift_warn_pct": 40.0}),
             _diag("performance", SEVERITY_OK), _diag("cost", SEVERITY_OK),
             _diag("signal", SEVERITY_OK), _diag("drawdown", SEVERITY_OK),
             _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg(regime_mix_shift_warn_pct=40.0))
    kinds = [s.kind for s in sugs]
    assert "review_parameters" in kinds


def test_revalidate_backtest_cost_plus_hit_rate():
    diags = [_diag("cost", SEVERITY_CRITICAL,
                   {"cost_drag_pct": 35.0}),
             _diag("performance", SEVERITY_OK,
                   {"hit_rate_pct": 38.0, "baseline_hit_rate_pct": 55.0}),
             _diag("regime", SEVERITY_OK), _diag("signal", SEVERITY_OK),
             _diag("drawdown", SEVERITY_OK), _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(
        diags, cfg=_cfg(cost_drag_critical_pct=30.0,
                       expected_baseline_hit_rate=55.0))
    kinds = [s.kind for s in sugs]
    assert "revalidate_backtest" in kinds


def test_priorities_sorted_ascending():
    diags = [_diag("performance", SEVERITY_OK), _diag("regime", SEVERITY_OK),
             _diag("cost", SEVERITY_OK), _diag("signal", SEVERITY_OK),
             _diag("drawdown", SEVERITY_OK), _diag("freshness", SEVERITY_OK)]
    sugs = compute_suggestions(diags, cfg=_cfg())
    # With all ok, we get keep_running only (priority 8)
    assert len(sugs) == 1
    assert sugs[0].priority == _PRIORITY[sugs[0].kind]


def test_diff_approvals_new_vs_superseded():
    from src.strategy_health.models import StrategySuggestion, APPROVAL_APPROVED, APPROVAL_PENDING
    sugs = tuple([
        StrategySuggestion(suggestion_id="s1", kind="keep_running", priority=8,
                           title="t", rationale="r"),
        StrategySuggestion(suggestion_id="s2", kind="watch_only", priority=7,
                           title="t", rationale="r"),
    ])
    existing = {
        "s1": {"suggestion_id": "s1", "status": APPROVAL_APPROVED,
               "created_at": "", "updated_at": ""},
    }
    new_sugs, pending = diff_approvals(sugs, existing)
    assert len(new_sugs) == 1
    assert new_sugs[0].suggestion_id == "s2"
    assert len(pending) == 0