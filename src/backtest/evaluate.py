"""Outcome evaluation — compute Outcome rows from (signal, horizon, entry, exit, reason).

Pure function: no I/O, no API calls. Fully deterministic.
"""
from __future__ import annotations

from typing import Generator, Optional

from .models import (
    Outcome,
    DECISION_LONG,
    DECISION_SHORT,
    DECISION_NONE,
    OUTCOME_OK,
    OUTCOME_OUT_OF_WINDOW,
    OUTCOME_DECISION_NONE,
    OUTCOME_MISSING_FIELDS,
)


def evaluate_outcomes(
    signals_with_prices: Generator[
        tuple[dict, int, Optional[float], Optional[float], str], None, None
    ],
    include_none_decision: bool = False,
) -> Generator[Outcome, None, None]:
    """Compute Outcome rows from walk_forward tuples.

    Args:
        signals_with_prices: yields (signal_dict, horizon_bars, entry_price,
                                    exit_price, outcome_reason)
        include_none_decision: if True, decision=none rows are yielded (for
                               distribution analysis); if False, skipped.

    Yields:
        Outcome instances — one per (signal, horizon) tuple.
    """
    for signal, horizon, entry_price, exit_price, reason in signals_with_prices:
        outcome = _compute_one(
            signal, horizon, entry_price, exit_price, reason, include_none_decision
        )
        if outcome is not None:
            yield outcome


def _compute_one(
    signal: dict,
    horizon_bars: int,
    entry_price: Optional[float],
    exit_price: Optional[float],
    outcome_reason: str,
    include_none: bool,
) -> Optional[Outcome]:
    """Single signal × horizon → Outcome row."""
    decision = signal.get("decision", DECISION_NONE)

    if decision == DECISION_NONE and not include_none:
        return None

    raw_return: Optional[float] = None
    signed_return: Optional[float] = None
    direction_correct: Optional[bool] = None
    move_abs: Optional[float] = None

    if entry_price is not None and exit_price is not None:
        raw = (exit_price - entry_price) / entry_price
        raw_return = raw
        move_abs = abs(raw)

        if decision == DECISION_LONG:
            signed_return = raw
            direction_correct = raw > 0
        elif decision == DECISION_SHORT:
            signed_return = -raw
            direction_correct = raw < 0

    return Outcome(
        signal_id=signal.get("signal_id", ""),
        signal_ts=signal.get("timestamp", ""),
        decision=decision,
        horizon_bars=horizon_bars,
        entry_price=entry_price,
        exit_price=exit_price,
        raw_return=raw_return,
        signed_return=signed_return,
        direction_correct=direction_correct,
        move_abs=move_abs,
        confidence=float(signal.get("confidence", 0.0)),
        trade_candidate=bool(signal.get("trade_candidate")),
        consensus_label=signal.get("consensus_label", ""),
        conflict_label=signal.get("conflict_label", ""),
        regime_tag=signal.get("regime_tag"),
        timeframe=signal.get("timeframe", "1D"),
        data_quality=signal.get("data_quality"),
        outcome_reason=outcome_reason,
    )