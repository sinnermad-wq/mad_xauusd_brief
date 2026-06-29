"""Tests for breakdown — compute_breakdown group stats.

Key properties tested:
- consensus label breakdown
- conflict label breakdown
- trade_candidate breakdown
- decision breakdown
- horizon breakdown
- verdict = INSUFFICIENT_DATA when < 10 trade rows
"""
import pytest
from backtest.breakdown import compute_breakdown
from backtest.models import (
    Outcome,
    DECISION_LONG, DECISION_SHORT, DECISION_NONE,
    VERDICT_OK, VERDICT_INSUFFICIENT,
)


def _make(
    decision=DECISION_LONG,
    confidence=0.7,
    direction_correct=True,
    consensus_label="aligned",
    conflict_label="none",
    trade_candidate=True,
    data_quality="ok",
    horizon=1,
) -> Outcome:
    raw = 0.05 if direction_correct else -0.05
    return Outcome(
        signal_id="sig-test",
        signal_ts="2026-06-27T00:00:00Z",
        decision=decision,
        horizon_bars=horizon,
        entry_price=100.0,
        exit_price=100.0 * (1 + raw),
        raw_return=raw,
        signed_return=raw if decision == DECISION_LONG else -raw,
        direction_correct=direction_correct,
        move_abs=abs(raw),
        confidence=confidence,
        trade_candidate=trade_candidate,
        consensus_label=consensus_label,
        conflict_label=conflict_label,
        regime_tag=None,
        timeframe="1D",
        data_quality=data_quality,
        outcome_reason="ok",
    )


def test_consensus_breakdown_hit_rates():
    """aligned=2 correct, insufficient_context=1 wrong → hit_rates differ"""
    outcomes = [
        _make(consensus_label="aligned",                 direction_correct=True),
        _make(consensus_label="aligned",                 direction_correct=True),
        _make(consensus_label="insufficient_context",   direction_correct=False),
    ]
    bd = compute_breakdown(outcomes, horizons=(1,))
    table = bd["by_consensus_label"]
    aligned_row = next(r for r in table.rows if r.label == "aligned")
    insufficient_row = next(r for r in table.rows if r.label == "insufficient_context")
    assert aligned_row.hit_rate == 1.0
    assert insufficient_row.hit_rate == 0.0
    assert aligned_row.n == 2
    assert insufficient_row.n == 1


def test_conflict_breakdown_none_vs_has_conflict():
    """conflict=none rows have different hit rate than conflict rows."""
    outcomes = (
        [_make(conflict_label="none",                 direction_correct=True)  for _ in range(3)] +
        [_make(conflict_label="missing_briefing",       direction_correct=False) for _ in range(2)]
    )
    bd = compute_breakdown(outcomes, horizons=(1,))
    table = bd["by_conflict_label"]
    labels = {r.label for r in table.rows}
    assert "none" in labels
    assert "missing_briefing" in labels


def test_trade_candidate_split():
    """trade_candidate=True and False rows grouped correctly."""
    outcomes = (
        [_make(trade_candidate=True,  direction_correct=True)  for _ in range(5)] +
        [_make(trade_candidate=False, direction_correct=False) for _ in range(2)]
    )
    bd = compute_breakdown(outcomes, horizons=(1,))
    table = bd["by_trade_candidate"]
    rows = {r.label: r for r in table.rows}
    assert rows["True"].n == 5
    assert rows["False"].n == 2
    assert rows["True"].hit_rate == 1.0


def test_decision_breakdown_long_short():
    """Long vs short rows counted correctly."""
    outcomes = [
        _make(decision=DECISION_LONG,  direction_correct=True),
        _make(decision=DECISION_SHORT, direction_correct=True),
        _make(decision=DECISION_SHORT, direction_correct=False),
    ]
    bd = compute_breakdown(outcomes, horizons=(1,))
    table = bd["by_decision"]
    rows = {r.label: r for r in table.rows}
    assert rows["long"].n == 1
    assert rows["short"].n == 2
    assert rows["long"].hit_rate == 1.0
    assert rows["short"].hit_rate == 0.5


def test_by_horizon_groups():
    """1-bar vs 3-bar outcomes grouped separately."""
    outcomes = [
        _make(horizon=1, direction_correct=True),
        _make(horizon=1, direction_correct=True),
        _make(horizon=3, direction_correct=False),
    ]
    bd = compute_breakdown(outcomes, horizons=(1, 3))
    table = bd["by_horizon"]
    rows = {r.label: r for r in table.rows}
    assert rows["1-bar"].n == 2
    assert rows["1-bar"].hit_rate == 1.0
    assert rows["3-bar"].n == 1
    assert rows["3-bar"].hit_rate == 0.0


def test_breakdown_verdict_insufficient_under_10():
    """<10 trade rows → INSUFFICIENT_DATA on each table."""
    outcomes = [_make() for _ in range(5)]
    bd = compute_breakdown(outcomes, horizons=(1,))
    for dim, table in bd.items():
        assert table.verdict == VERDICT_INSUFFICIENT


def test_breakdown_verdict_ok_10_plus():
    """>=10 trade rows → OK on each table."""
    outcomes = [_make() for _ in range(12)]
    bd = compute_breakdown(outcomes, horizons=(1,))
    for dim, table in bd.items():
        assert table.verdict == VERDICT_OK


def test_pct_of_total_sums_to_1():
    """pct_of_total across all rows sums to ~1.0 (floating point tolerance)."""
    outcomes = (
        [_make(consensus_label="aligned") for _ in range(6)] +
        [_make(consensus_label="divergent") for _ in range(4)]
    )
    bd = compute_breakdown(outcomes, horizons=(1,))
    table = bd["by_consensus_label"]
    total_pct = sum(r.pct_of_total for r in table.rows)
    assert total_pct == pytest.approx(1.0, abs=1e-4)