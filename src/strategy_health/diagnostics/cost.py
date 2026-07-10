"""Cost / execution drag diagnostic.

Computes aggregate cost drag from per-trade ``cost_pct`` (or similar
slippage+fee fields) and compares to backtest expectancy. Without a
real execution layer this is an analytical proxy built from backtest
records; numbers represent *estimated* cost erosion, not realised.

Inputs: trade list with optional fields::

    {"pnl_pct": 0.5, "cost_pct": 0.05, "slippage_pct": 0.02,
     "latency_pct": 0.01, "exit_time": "2026-07-09T00:00:00Z"}

Returns one ``StrategyDiagnostic``.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SOURCE_BACKTEST,
    StrategyDiagnostic,
)


COST_FIELDS = ("cost_pct", "slippage_pct", "latency_pct", "fee_pct")


def _coerce_trade(rec: Mapping[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(rec, Mapping):
        return None
    pnl = _safe_float(rec.get("pnl_pct"))
    if pnl is None and "pnl" in rec:
        pnl = _safe_float(rec["pnl"])
    if pnl is None and "return_pct" in rec:
        pnl = _safe_float(rec["return_pct"])
    if pnl is None:
        return None
    costs = {k: _safe_float(rec.get(k)) or 0.0 for k in COST_FIELDS}
    return {"pnl_pct": pnl, **costs}


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _extract_trades_obj(backtest_report: Mapping[str, Any] | None) -> List[Mapping[str, Any]]:
    if not backtest_report:
        return []
    if isinstance(backtest_report, list):
        return [t for t in backtest_report if isinstance(t, Mapping)]
    if isinstance(backtest_report.get("trades"), list):
        return [t for t in backtest_report["trades"] if isinstance(t, Mapping)]
    if isinstance(backtest_report.get("outcomes"), list):
        return [t for t in backtest_report["outcomes"] if isinstance(t, Mapping)]
    stats = backtest_report.get("stats") if isinstance(backtest_report, Mapping) else None
    if isinstance(stats, Mapping) and isinstance(stats.get("trades"), list):
        return [t for t in stats["trades"] if isinstance(t, Mapping)]
    return []


def _window_trades(backtest_report: Mapping[str, Any] | None, window: int) -> List[Dict[str, Any]]:
    raw = _extract_trades_obj(backtest_report)
    cleaned = [t for t in (_coerce_trade(r) for r in raw) if t is not None]
    if window <= 0 or not cleaned:
        return cleaned
    return cleaned[-window:]


def compute_cost_diagnostic(
    backtest_report: Mapping[str, Any] | None,
    cfg,
) -> StrategyDiagnostic:
    """Estimate cost drag using per-trade cost fields + windowed expectancy.

    No real execution layer — purely analytical from backtest records.
    """
    window = int(getattr(cfg, "cost_window", 50))
    warn_thr = float(getattr(cfg, "cost_drag_warn_pct", 10.0))
    crit_thr = float(getattr(cfg, "cost_drag_critical_pct", 30.0))

    rows = _window_trades(backtest_report, window)
    if not rows:
        return StrategyDiagnostic(
            name="cost",
            severity=SEVERITY_UNKNOWN,
            summary="No cost-tagged trades available — cost drag cannot be evaluated.",
            metrics={"window": window, "samples": 0},
            reasons=("missing_cost_data",),
            source=SOURCE_BACKTEST,
        )

    total_pnl = sum(r["pnl_pct"] for r in rows)
    total_cost = sum(sum(r[k] for k in COST_FIELDS) for r in rows)
    n = len(rows)

    # Cost drag share: cost / (pnl + cost).  Pure proxy, illustrative.
    denominator = total_pnl + total_cost
    if denominator > 0:
        cost_drag_pct = (total_cost / denominator) * 100.0
    else:
        cost_drag_pct = 100.0 if total_cost > 0 else 0.0

    gross_pnl = total_pnl
    net_pnl = total_pnl - total_cost
    avg_cost_per_trade = total_cost / n
    avg_pnl_per_trade = gross_pnl / n

    metrics: Dict[str, Any] = {
        "window": window,
        "samples": n,
        "total_pnl_pct": round(total_pnl, 4),
        "total_cost_pct": round(total_cost, 4),
        "net_pnl_pct": round(net_pnl, 4),
        "cost_drag_pct": round(cost_drag_pct, 2),
        "avg_cost_per_trade_pct": round(avg_cost_per_trade, 4),
        "avg_pnl_per_trade_pct": round(avg_pnl_per_trade, 4),
        "warn_threshold_pct": warn_thr,
        "critical_threshold_pct": crit_thr,
    }

    reasons: List[str] = []
    severity = SEVERITY_OK

    if cost_drag_pct >= crit_thr:
        severity = SEVERITY_CRITICAL
        reasons.append("cost_drag_critical")
    elif cost_drag_pct >= warn_thr:
        severity = SEVERITY_WARN
        reasons.append("cost_drag_warn")

    if avg_pnl_per_trade > 0 and avg_cost_per_trade >= abs(avg_pnl_per_trade) * 0.5:
        if severity != SEVERITY_CRITICAL:
            severity = SEVERITY_WARN
        reasons.append("cost_nearly_erodes_edge")

    summary_text = (
        f"cost window {n} trades; total cost {total_cost:.3f}% / "
        f"net PnL {net_pnl:.3f}%; drag {cost_drag_pct:.2f}%; "
        f"verdict={severity}"
    )

    return StrategyDiagnostic(
        name="cost",
        severity=severity,
        summary=summary_text,
        metrics=metrics,
        reasons=tuple(reasons) or ("cost_within_acceptable_range",),
        source=SOURCE_BACKTEST,
    )
