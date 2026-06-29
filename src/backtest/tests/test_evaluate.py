"""Tests for evaluate — evaluate_outcomes + _compute_one.

Key properties tested:
- long decision: direction_correct = raw_return > 0
- short decision: direction_correct = raw_return < 0 (signed = -raw)
- none decision: skipped by default; included when include_none_decision=True
- out_of_window: entry set, exit=None, raw=None
- outcome_reason propagation
"""
import pytest
from backtest.evaluate import evaluate_outcomes
from backtest.models import DECISION_LONG, DECISION_SHORT, DECISION_NONE


def _sig(decision=DECISION_LONG, confidence=0.7, trade_candidate=True, **kw):
    base = {
        "signal_id": "sig-test",
        "timestamp": "2026-06-26T00:00:00Z",
        "decision": decision,
        "confidence": confidence,
        "trade_candidate": trade_candidate,
        "consensus_label": "aligned",
        "conflict_label": "none",
        "regime_tag": None,
        "data_quality": "ok",
    }
    base.update(kw)
    return base


def _emit(*items):
    """Yield from generator to list."""
    return list(evaluate_outcomes(iter(items), include_none_decision=False))


def _emit_with_none(*items):
    return list(evaluate_outcomes(iter(items), include_none_decision=True))


# ─── long decision ────────────────────────────────────────────────────────
def test_long_correct_when_price_rises():
    # entry 100, exit 110 → raw=+0.1, direction_correct=True
    sig = _sig(decision=DECISION_LONG)
    out = _emit((sig, 1, 100.0, 110.0, "ok"))
    assert len(out) == 1
    o = out[0]
    assert o.direction_correct is True
    assert o.signed_return == pytest.approx(0.1)
    assert o.raw_return == pytest.approx(0.1)
    assert o.outcome_reason == "ok"


def test_long_incorrect_when_price_falls():
    # entry 100, exit 90 → raw=-0.1, direction_correct=False
    sig = _sig(decision=DECISION_LONG)
    out = _emit((sig, 1, 100.0, 90.0, "ok"))
    assert len(out) == 1
    assert out[0].direction_correct is False
    assert out[0].signed_return == pytest.approx(-0.1)
    assert out[0].raw_return == pytest.approx(-0.1)


# ─── short decision ──────────────────────────────────────────────────────
def test_short_correct_when_price_falls():
    # entry 100, exit 90 → raw=-0.1, signed=+0.1, direction_correct=True (price fell = short wins)
    sig = _sig(decision=DECISION_SHORT)
    out = _emit((sig, 1, 100.0, 90.0, "ok"))
    assert len(out) == 1
    assert out[0].direction_correct is True
    assert out[0].signed_return == pytest.approx(0.1)
    assert out[0].raw_return == pytest.approx(-0.1)


def test_short_incorrect_when_price_rises():
    # entry 100, exit 110 → raw=+0.1, signed=-0.1, direction_correct=False
    sig = _sig(decision=DECISION_SHORT)
    out = _emit((sig, 1, 100.0, 110.0, "ok"))
    assert len(out) == 1
    assert out[0].direction_correct is False
    assert out[0].signed_return == pytest.approx(-0.1)


# ─── none decision ────────────────────────────────────────────────────────
def test_none_skipped_by_default():
    sig = _sig(decision=DECISION_NONE)
    out = _emit((sig, 1, 100.0, 110.0, "ok"))
    assert len(out) == 0


def test_none_included_when_flag_true():
    sig = _sig(decision=DECISION_NONE, confidence=0.0, trade_candidate=False)
    out = _emit_with_none((sig, 1, 100.0, 110.0, "decision_none_skip_trade"))
    assert len(out) == 1
    assert out[0].decision == DECISION_NONE
    assert out[0].outcome_reason == "decision_none_skip_trade"


# ─── out_of_window ───────────────────────────────────────────────────────
def test_out_of_window_preserves_entry_exit_none():
    sig = _sig()
    out = _emit((sig, 5, 100.0, None, "out_of_window"))
    assert len(out) == 1
    assert out[0].entry_price == 100.0
    assert out[0].exit_price is None
    assert out[0].raw_return is None
    assert out[0].outcome_reason == "out_of_window"


# ─── multi-horizon ───────────────────────────────────────────────────────
def test_multi_horizon_expands_to_multiple_rows():
    sig = _sig()
    out = _emit(
        (sig, 1, 100.0, 102.0, "ok"),
        (sig, 3, 100.0, 108.0, "ok"),
        (sig, 5, 100.0, 110.0, "ok"),
    )
    assert len(out) == 3
    assert [o.horizon_bars for o in out] == [1, 3, 5]
    # signed_return for long: raw
    assert out[0].signed_return == pytest.approx(0.02)
    assert out[1].signed_return == pytest.approx(0.08)
    assert out[2].signed_return == pytest.approx(0.10)


# ─── confidence/trade_candidate passthrough ─────────────────────────────
def test_confidence_and_trade_candidate_passthrough():
    sig = _sig(decision=DECISION_LONG, confidence=0.75, trade_candidate=True)
    out = _emit((sig, 1, 100.0, 110.0, "ok"))
    assert out[0].confidence == 0.75
    assert out[0].trade_candidate is True