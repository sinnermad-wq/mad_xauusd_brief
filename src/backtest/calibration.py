"""Confidence calibration analysis — bucket statistics, ECE, Brier score.

Pure functions: list[Outcome] → CalibrationReport.
No I/O, no side-effects, fully deterministic.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from .models import (
    Outcome,
    CalibrationBucket,
    CalibrationReport,
    DECISION_LONG,
    DECISION_SHORT,
    DECISION_NONE,
    VERDICT_OK,
    VERDICT_INSUFFICIENT,
)


# ─── Constants ───────────────────────────────────────────────────────────
BUCKET_STEP = 0.1          # 0.0–0.1, 0.1–0.2, …, 0.9–1.0
BUCKET_EDGES = [round(i * BUCKET_STEP, 2) for i in range(11)]  # 0.0 … 1.0


def _make_buckets() -> List[Tuple[float, float]]:
    return [(BUCKET_EDGES[i], BUCKET_EDGES[i + 1]) for i in range(len(BUCKET_EDGES) - 1)]


def _bucket_label(lo: float, hi: float) -> str:
    return f"[{lo:.1f},{hi:.1f})"


# ─── Main entry ──────────────────────────────────────────────────────────
def compute_calibration(
    outcomes: List[Outcome],
    bucket_step: float = BUCKET_STEP,
) -> CalibrationReport:
    """Compute CalibrationReport from outcome list.

    Only rows with outcome_reason == OUTCOME_OK are included in statistics.
    decision=none rows are counted in n_none but excluded from hit_rate calculation.

    Returns CalibrationReport with verdict = VERDICT_OK if n_total >= 10,
    else VERDICT_INSUFFICIENT.
    """
    ok_outcomes = [o for o in outcomes if o.outcome_reason == "ok"]
    trade_rows = [o for o in ok_outcomes if o.decision != DECISION_NONE]
    none_rows  = [o for o in ok_outcomes if o.decision == DECISION_NONE]

    # ── bucket stats ──────────────────────────────────────────────────────
    buckets_raw = _bucket_outcomes(trade_rows, bucket_step)
    buckets = tuple(
        CalibrationBucket(
            lo=lo, hi=hi, n=n,
            n_long=n_long, n_short=n_short, n_none=0,
            hit_rate=hit_rate,
            avg_signed_return=avg_signed,
            avg_raw_return=avg_raw,
            avg_confidence=avg_conf,
        )
        for (lo, hi), (n, n_long, n_short, hit_rate, avg_signed, avg_raw, avg_conf)
        in buckets_raw.items()
    )
    buckets = _fill_missing_buckets(buckets, bucket_step)

    # ── aggregate stats ────────────────────────────────────────────────────
    n_total = len(trade_rows)
    n_long  = sum(1 for o in trade_rows if o.decision == DECISION_LONG)
    n_short = sum(1 for o in trade_rows if o.decision == DECISION_SHORT)

    # ── ECE ──────────────────────────────────────────────────────────────
    ece = _compute_ece(buckets, n_total)
    brier = _compute_brier(trade_rows)

    # ── trade_candidate filter ──────────────────────────────────────────
    tc_hit = _by_flag(trade_rows, lambda o: o.trade_candidate)

    # ── consensus / conflict ─────────────────────────────────────────────
    consensus_hit = _by_label(trade_rows, lambda o: o.consensus_label)
    conflict_hit   = _by_label(trade_rows, lambda o: o.conflict_label)

    verdict = VERDICT_OK if n_total >= 10 else VERDICT_INSUFFICIENT

    return CalibrationReport(
        buckets=buckets,
        ece=round(ece, 6),
        brier=round(brier, 6),
        n_total=n_total,
        n_long=n_long,
        n_short=n_short,
        n_none=len(none_rows),
        by_trade_candidate_hit_rate=tc_hit,
        by_consensus_hit_rate=consensus_hit,
        by_conflict_hit_rate=conflict_hit,
        verdict=verdict,
    )


# ─── Internal helpers ────────────────────────────────────────────────────
def _bucket_outcomes(
    outcomes: List[Outcome],
    step: float,
) -> Dict[Tuple[float, float], Tuple[int, int, int, float, float, float, float]]:
    """Group trade outcomes by confidence bucket.

    Returns dict: (lo, hi) → (n, n_long, n_short, hit_rate,
                               avg_signed_return, avg_raw_return, avg_confidence)
    """
    groups: Dict[Tuple[float, float], List[Outcome]] = defaultdict(list)
    for o in outcomes:
        lo, hi = _find_bucket(o.confidence, step)
        if lo is not None:
            groups[(lo, hi)].append(o)

    result = {}
    for (lo, hi), items in sorted(groups.items()):
        n = len(items)
        n_long  = sum(1 for o in items if o.decision == DECISION_LONG)
        n_short = sum(1 for o in items if o.decision == DECISION_SHORT)

        hits = [o for o in items if o.direction_correct is True]
        hit_rate = len(hits) / n if n > 0 else 0.0

        signed_returns = [o.signed_return for o in items if o.signed_return is not None]
        raw_returns    = [o.raw_return for o in items if o.raw_return is not None]
        confidences    = [o.confidence for o in items]

        avg_signed = sum(signed_returns) / len(signed_returns) if signed_returns else 0.0
        avg_raw    = sum(raw_returns)    / len(raw_returns)    if raw_returns    else 0.0
        avg_conf   = sum(confidences)     / len(confidences)     if confidences     else 0.0

        result[(lo, hi)] = (n, n_long, n_short, hit_rate, avg_signed, avg_raw, avg_conf)

    return result


def _find_bucket(confidence: float, step: float) -> Tuple[float, float]:
    """Return (lo, hi) for a confidence value, or (None,None) if out of range."""
    for i in range(len(BUCKET_EDGES) - 1):
        lo, hi = BUCKET_EDGES[i], BUCKET_EDGES[i + 1]
        if lo <= confidence < hi:
            return (round(lo, 2), round(hi, 2))
    # confidence == 1.0 → last bucket [0.9, 1.0)
    if confidence == 1.0:
        return (round(BUCKET_EDGES[-2], 2), round(BUCKET_EDGES[-1], 2))
    return (None, None)


def _fill_missing_buckets(
    buckets: Tuple[CalibrationBucket, ...],
    step: float,
) -> Tuple[CalibrationBucket, ...]:
    """Ensure every defined bucket edge pair has an entry (n=0 if not present)."""
    defined = {(b.lo, b.hi) for b in buckets}
    all_buckets = list(buckets)
    for lo, hi in _make_buckets():
        if (lo, hi) not in defined:
            all_buckets.append(CalibrationBucket(
                lo=lo, hi=hi, n=0,
                n_long=0, n_short=0, n_none=0,
                hit_rate=0.0, avg_signed_return=0.0,
                avg_raw_return=0.0, avg_confidence=lo + step / 2,
            ))
    all_buckets.sort(key=lambda b: b.lo)
    return tuple(all_buckets)


def _compute_ece(buckets: Tuple[CalibrationBucket, ...], n_total: int) -> float:
    """Expected Calibration Error: Σ (n_i/N) × |hit_rate_i − avg_confidence_i|."""
    if n_total == 0:
        return 0.0
    ece = 0.0
    for b in buckets:
        weight = b.n / n_total
        ece += weight * abs(b.hit_rate - b.avg_confidence)
    return ece


def _compute_brier(outcomes: List[Outcome]) -> float:
    """Brier-like: mean((hit − confidence)²). hit ∈ {0, 1}, confidence ∈ [0, 1]."""
    if not outcomes:
        return 0.0
    total = 0.0
    for o in outcomes:
        hit = 1.0 if o.direction_correct else 0.0
        total += (hit - o.confidence) ** 2
    return total / len(outcomes)


def _by_flag(outcomes: List[Outcome],
             key_fn) -> Dict[bool, float]:
    """Hit_rate by boolean flag (e.g. trade_candidate)."""
    groups: Dict[bool, List[Outcome]] = defaultdict(list)
    for o in outcomes:
        groups[key_fn(o)].append(o)
    return {
        flag: _hit_rate(items)
        for flag, items in groups.items()
    }


def _by_label(outcomes: List[Outcome],
              key_fn) -> Dict[str, float]:
    """Hit_rate by string label (e.g. consensus_label)."""
    groups: Dict[str, List[Outcome]] = defaultdict(list)
    for o in outcomes:
        groups[key_fn(o)].append(o)
    return {
        label: _hit_rate(items)
        for label, items in groups.items()
    }


def _hit_rate(outcomes: List[Outcome]) -> float:
    if not outcomes:
        return 0.0
    hits = sum(1 for o in outcomes if o.direction_correct is True)
    return hits / len(outcomes)