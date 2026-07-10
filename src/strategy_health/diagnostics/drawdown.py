"""Drawdown / risk-stress diagnostic.

Builds an *equity curve* from per-trade PnL percentages (compounded),
then computes:

* max drawdown  (% from peak)
* current drawdown
* recovery factor (net_pnl / max_dd)
* window-days coverage

Inputs: trade list with ``exit_time`` (ISO string) and ``pnl_pct``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SOURCE_BACKTEST,
    StrategyDiagnostic,
)


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


def _safe_dt(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.replace("Z", "+00:00") if v.endswith("Z") else v
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _coerce(rec: Mapping[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(rec, Mapping):
        return None
    pnl = _safe_float(rec.get("pnl_pct"))
    if pnl is None:
        return None
    return {
        "pnl_pct": pnl,
        "dt": _safe_dt(rec.get("exit_time")) or _safe_dt(rec.get("signal_time")),
    }


def _extract(backtest_report: Mapping[str, Any] | None) -> List[Mapping[str, Any]]:
    if not backtest_report:
        return []
    rows: List[Mapping[str, Any]] = []
    if isinstance(backtest_report, list):
        rows = [t for t in backtest_report if isinstance(t, Mapping)]
    elif isinstance(backtest_report.get("trades"), list):
        rows = [t for t in backtest_report["trades"] if isinstance(t, Mapping)]
    elif isinstance(backtest_report.get("outcomes"), list):
        rows = [t for t in backtest_report["outcomes"] if isinstance(t, Mapping)]
    elif isinstance(backtest_report.get("stats"), Mapping):
        st = backtest_report["stats"]
        if isinstance(st.get("trades"), list):
            rows = [t for t in st["trades"] if isinstance(t, Mapping)]
    return rows


def _within_window(rows: Sequence[Mapping[str, Any]], window_days: int) -> List[Dict[str, Any]]:
    if window_days <= 0:
        return list(rows)
    cleaned: List[Dict[str, Any]] = []
    dts: List[datetime] = []
    for r in rows:
        c = _coerce(r)
        if c is None:
            continue
        cleaned.append(c)
        if c["dt"] is not None:
            dts.append(c["dt"])
    if not dts:
        return cleaned
    latest = max(dts)
    cutoff = latest.timestamp() - window_days * 86400.0
    return [c for c in cleaned if c["dt"] is None or c["dt"].timestamp() >= cutoff]


def _equity_curve(rows: Sequence[Mapping[str, Any]]) -> List[float]:
    """Compounded equity (% return) starting at 1.0."""
    eq = 1.0
    out = [1.0]
    for r in rows:
        pnl = r["pnl_pct"]
        eq *= 1.0 + pnl / 100.0
        out.append(eq)
    return out


def _drawdown_stats(curve: Sequence[float]) -> Dict[str, float]:
    if not curve:
        return {"max_dd_pct": 0.0, "current_dd_pct": 0.0, "recovery_factor": 0.0}
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    current_dd = (peak - curve[-1]) / peak * 100.0 if peak > 0 else 0.0
    total_return_pct = (curve[-1] - 1.0) * 100.0
    recovery = (total_return_pct / max_dd) if max_dd > 0 else 0.0
    return {
        "max_dd_pct": round(max_dd, 2),
        "current_dd_pct": round(current_dd, 2),
        "recovery_factor": round(recovery, 2),
    }


def compute_drawdown_diagnostic(
    backtest_report: Mapping[str, Any] | None,
    cfg,
) -> StrategyDiagnostic:
    window_days = int(getattr(cfg, "drawdown_window_days", 90))
    warn_thr = float(getattr(cfg, "drawdown_warn_pct", 7.5))
    crit_thr = float(getattr(cfg, "drawdown_critical_pct", 15.0))

    rows_raw = _extract(backtest_report)
    rows = _within_window(rows_raw, window_days)
    cleaned = [c for c in rows if c.get("pnl_pct") is not None]
    if not cleaned:
        return StrategyDiagnostic(
            name="drawdown",
            severity=SEVERITY_UNKNOWN,
            summary="No trades available — drawdown cannot be evaluated.",
            metrics={"window_days": window_days, "samples": 0},
            reasons=("missing_backtest_trades",),
            source=SOURCE_BACKTEST,
        )

    cleaned.sort(key=lambda r: r["dt"] or datetime.min.replace(tzinfo=timezone.utc))
    curve = _equity_curve(cleaned)
    stats = _drawdown_stats(curve)

    metrics: Dict[str, Any] = {
        "window_days": window_days,
        "samples": len(cleaned),
        "max_dd_pct": stats["max_dd_pct"],
        "current_dd_pct": stats["current_dd_pct"],
        "recovery_factor": stats["recovery_factor"],
        "warn_threshold_pct": warn_thr,
        "critical_threshold_pct": crit_thr,
    }

    reasons: List[str] = []
    severity = SEVERITY_OK

    if stats["max_dd_pct"] >= crit_thr:
        severity = SEVERITY_CRITICAL
        reasons.append("max_drawdown_critical")
    elif stats["max_dd_pct"] >= warn_thr:
        severity = SEVERITY_WARN
        reasons.append("max_drawdown_warn")

    if stats["current_dd_pct"] >= crit_thr / 2.0:
        if severity == SEVERITY_OK:
            severity = SEVERITY_WARN
        reasons.append("active_drawdown_present")

    summary_text = (
        f"window {window_days} days / {len(cleaned)} trades; "
        f"max_dd {stats['max_dd_pct']:.2f}%; "
        f"current_dd {stats['current_dd_pct']:.2f}%; "
        f"recovery_factor {stats['recovery_factor']:.2f}; "
        f"verdict={severity}"
    )

    return StrategyDiagnostic(
        name="drawdown",
        severity=severity,
        summary=summary_text,
        metrics=metrics,
        reasons=tuple(reasons) or ("drawdown_within_thresholds",),
        source=SOURCE_BACKTEST,
    )
