"""Performance diagnostic.

Rolling window over the most-recent closed trades from a backtest report.
Compares:
  * rolling hit-rate vs ``expected_baseline_hit_rate`` (cfg)
  * rolling avg gain vs avg loss
  * rolling expectancy

Inputs: dict produced by ``backtest/evaluate`` — accepts either the
cannonical Report schema (``stats.overall``) or a flattened
``{"trades": [...]}`` view; the ``extract_trades`` helper normalises
them.

NO side effects. Pure function.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from ..models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SOURCE_BACKTEST,
    StrategyDiagnostic,
)


def _extract_trades(backtest_report: Mapping[str, Any] | None) -> List[Dict[str, Any]]:
    """Return list of normalised trade dicts from a backtest report.

    Accepted shapes::

        {"trades": [ {pnl_pct, exit_time, ...}, ... ]}
        {"stats": {"overall": {...}, "trades": [...]}}
        {"outcomes": [ {...row...} ]}
    """
    if not backtest_report:
        return []
    if isinstance(backtest_report, list):
        return [t for t in backtest_report if isinstance(t, Mapping)]
    if "trades" in backtest_report and isinstance(backtest_report["trades"], list):
        return [t for t in backtest_report["trades"] if isinstance(t, Mapping)]
    if "outcomes" in backtest_report and isinstance(backtest_report["outcomes"], list):
        return [t for t in backtest_report["outcomes"] if isinstance(t, Mapping)]
    stats = backtest_report.get("stats") if isinstance(backtest_report, Mapping) else None
    if isinstance(stats, Mapping):
        if isinstance(stats.get("trades"), list):
            return [t for t in stats["trades"] if isinstance(t, Mapping)]
    return []


def _trade_pnl(trade: Mapping[str, Any]) -> float | None:
    for key in ("pnl_pct", "pnl", "gain_pct", "return_pct"):
        if key in trade and trade[key] is not None:
            try:
                return float(trade[key])
            except (TypeError, ValueError):
                return None
    return None


def _rolling_window(trades: Sequence[Mapping[str, Any]], window: int) -> List[Dict[str, Any]]:
    if window <= 0 or not trades:
        return []
    return list(trades[-window:])


def _summarise(window: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    pnls = [p for p in (_trade_pnl(t) for t in window) if p is not None]
    if not pnls:
        return {
            "n": 0,
            "hit_rate": 0.0,
            "avg_pnl": 0.0,
            "avg_gain": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)
    hit_rate = (len(wins) / n) * 100.0
    avg_pnl = sum(pnls) / n
    avg_gain = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = avg_pnl
    return {
        "n": n,
        "hit_rate": hit_rate,
        "avg_pnl": avg_pnl,
        "avg_gain": avg_gain,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
    }


def compute_performance_diagnostic(
    backtest_report: Mapping[str, Any] | None,
    cfg,
) -> StrategyDiagnostic:
    """Compute a StrategyDiagnostic for rolling performance."""
    window = int(getattr(cfg, "performance_window", 20))
    baseline = float(getattr(cfg, "expected_baseline_hit_rate", 55.0))

    trades = _extract_trades(backtest_report)
    if not trades:
        return StrategyDiagnostic(
            name="performance",
            severity=SEVERITY_UNKNOWN,
            summary="No trades available — performance cannot be evaluated.",
            metrics={"window": window, "samples": 0},
            reasons=("missing_backtest_trades",),
            source=SOURCE_BACKTEST,
        )

    roll = _rolling_window(trades, window)
    summary = _summarise(roll)
    metrics: Dict[str, Any] = {
        "window": window,
        "samples": summary["n"],
        "hit_rate_pct": round(summary["hit_rate"], 2),
        "avg_pnl_pct": round(summary["avg_pnl"], 4),
        "avg_gain_pct": round(summary["avg_gain"], 4),
        "avg_loss_pct": round(summary["avg_loss"], 4),
        "expectancy_pct": round(summary["expectancy"], 4),
        "baseline_hit_rate_pct": baseline,
    }
    reasons: List[str] = []
    severity = SEVERITY_OK

    if summary["n"] < max(5, window // 2):
        reasons.append("insufficient_samples")
        severity = SEVERITY_WARN

    if summary["n"] >= 5:
        delta = summary["hit_rate"] - baseline
        if delta <= -15.0:
            severity = SEVERITY_CRITICAL
            reasons.append("hit_rate_far_below_baseline")
        elif delta <= -7.5:
            if severity != SEVERITY_CRITICAL:
                severity = SEVERITY_WARN
            reasons.append("hit_rate_below_baseline")

    if summary["expectancy"] < 0 and severity == SEVERITY_OK:
        severity = SEVERITY_WARN
        reasons.append("negative_expectancy")

    summary_text = _build_summary_text(metrics, severity)
    return StrategyDiagnostic(
        name="performance",
        severity=severity,
        summary=summary_text,
        metrics=metrics,
        reasons=tuple(reasons) or ("within_baseline",),
        source=SOURCE_BACKTEST,
    )


def _build_summary_text(metrics: Dict[str, Any], severity: str) -> str:
    return (
        f"rolling {int(metrics['window'])}-trade window: "
        f"hit {metrics['hit_rate_pct']:.2f}% "
        f"(baseline {metrics['baseline_hit_rate_pct']:.2f}%), "
        f"expectancy {metrics['expectancy_pct']:.3f}%, "
        f"avg gain {metrics['avg_gain_pct']:.3f}% / "
        f"avg loss {metrics['avg_loss_pct']:.3f}%; "
        f"verdict={severity}"
    )
