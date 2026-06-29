"""Breakdown analysis — group outcomes by multiple dimensions.

Pure functions: list[Outcome] → BreakdownReport dict.
No I/O, fully deterministic.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple

from .models import Outcome, DECISION_NONE, VERDICT_OK, VERDICT_INSUFFICIENT


# ─── Result types ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BreakdownRow:
    label: str
    n: int
    hit_rate: float
    avg_signed_return: float
    avg_raw_return: float
    avg_confidence: float
    pct_of_total: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass(frozen=True)
class BreakdownTable:
    dimension: str
    rows: Tuple[BreakdownRow, ...]
    verdict: str

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension,
            "rows": [r.to_dict() for r in self.rows],
            "verdict": self.verdict,
        }


# ─── Main entry ─────────────────────────────────────────────────────────
def compute_breakdown(
    outcomes: List[Outcome],
    horizons: Tuple[int, ...],
) -> Dict[str, BreakdownTable]:
    """Compute BreakdownTable for each dimension.

    Args:
        outcomes: full outcome list (including decision=none)
        horizons: tuple of horizon bars (used for by_horizon breakdown)

    Returns:
        Dict keyed by dimension name:
            "by_consensus_label"
            "by_conflict_label"
            "by_trade_candidate"
            "by_decision"
            "by_horizon"
            "by_data_quality"
    """
    trade_rows = [o for o in outcomes if o.decision != DECISION_NONE]

    return {
        "by_consensus_label":  _breakdown_dim(trade_rows, "consensus_label",
                                               lambda o: o.consensus_label or "unknown"),
        "by_conflict_label":   _breakdown_dim(trade_rows, "conflict_label",
                                               lambda o: o.conflict_label or "unknown"),
        "by_trade_candidate":  _breakdown_dim(trade_rows, "trade_candidate",
                                               lambda o: str(o.trade_candidate)),
        "by_decision":          _breakdown_dim(trade_rows, "decision",
                                               lambda o: o.decision),
        "by_horizon":           _breakdown_dim(trade_rows, "horizon_bars",
                                               lambda o: f"{o.horizon_bars}-bar"),
        "by_data_quality":      _breakdown_dim(trade_rows, "data_quality",
                                               lambda o: o.data_quality or "unknown"),
    }


def _breakdown_dim(
    outcomes: List[Outcome],
    dimension_name: str,
    key_fn,
) -> BreakdownTable:
    """Build BreakdownTable for one dimension."""
    groups: Dict[str, List[Outcome]] = defaultdict(list)
    for o in outcomes:
        groups[key_fn(o)].append(o)

    n_total = len(outcomes)
    rows = []
    for label, items in sorted(groups.items()):
        n = len(items)
        hits = sum(1 for o in items if o.direction_correct is True)
        signed = [o.signed_return for o in items if o.signed_return is not None]
        raw    = [o.raw_return    for o in items if o.raw_return    is not None]
        confs  = [o.confidence    for o in items]

        rows.append(BreakdownRow(
            label=label,
            n=n,
            hit_rate=round(hits / n, 4) if n > 0 else 0.0,
            avg_signed_return=round(sum(signed) / len(signed), 6) if signed else 0.0,
            avg_raw_return=round(sum(raw)    / len(raw),    6) if raw    else 0.0,
            avg_confidence=round(sum(confs)  / len(confs),  4) if confs  else 0.0,
            pct_of_total=round(n / n_total, 4) if n_total > 0 else 0.0,
        ))

    verdict = VERDICT_OK if n_total >= 10 else VERDICT_INSUFFICIENT
    return BreakdownTable(dimension=dimension_name, rows=tuple(rows), verdict=verdict)