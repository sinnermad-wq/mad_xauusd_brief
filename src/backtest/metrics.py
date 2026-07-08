"""metrics.py — performance & risk metrics for backtest results.

Manual-only; no broker / execution / cron / dashboard wiring.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np

from .engine import TradeRecord


def win_rate(trades: List[TradeRecord]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.pnl_net > 0)
    return wins / len(trades) * 100


def profit_factor(trades: List[TradeRecord]) -> float:
    gross_gain = sum(t.pnl_net for t in trades if t.pnl_net > 0)
    gross_loss = abs(sum(t.pnl_net for t in trades if t.pnl_net < 0))
    if gross_loss == 0:
        return 0.0 if gross_gain == 0 else float("inf")
    return gross_gain / gross_loss


def expectancy(trades: List[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return sum(t.pnl_net for t in trades) / len(trades)


def sharpe_ratio(trades: List[TradeRecord], periods_per_year: int = 252 * 24) -> float:
    if len(trades) < 2:
        return 0.0
    returns = np.array([t.pnl_net for t in trades])
    mean_ret = returns.mean()
    std_ret = returns.std()
    if std_ret == 0:
        return 0.0
    return (mean_ret / std_ret) * math.sqrt(periods_per_year / max(len(trades), 1))


def sortino_ratio(trades: List[TradeRecord], periods_per_year: int = 252 * 24) -> float:
    if len(trades) < 2:
        return 0.0
    returns = np.array([t.pnl_net for t in trades])
    mean_ret = returns.mean()
    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        return float("inf") if mean_ret > 0 else 0.0
    return (mean_ret / downside.std()) * math.sqrt(periods_per_year / max(len(trades), 1))


def max_drawdown(trades: List[TradeRecord]) -> float:
    if not trades:
        return 0.0
    equity = np.cumsum([t.pnl_net for t in trades])
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return max_dd


def max_adverse_excursion(trades: List[TradeRecord]) -> float:
    """Average MAE across all trades."""
    if not trades:
        return 0.0
    return sum(t.mae for t in trades) / len(trades)


def max_favorable_excursion(trades: List[TradeRecord]) -> float:
    """Average MFE across all trades."""
    if not trades:
        return 0.0
    return sum(t.mfe for t in trades) / len(trades)


def mae_percentile(trades: List[TradeRecord], pct: float = 95) -> float:
    if not trades:
        return 0.0
    vals = sorted(t.mae for t in trades)
    k = (len(vals) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def mfe_percentile(trades: List[TradeRecord], pct: float = 95) -> float:
    if not trades:
        return 0.0
    vals = sorted(t.mfe for t in trades)
    k = (len(vals) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def session_breakdown(trades: List[TradeRecord]) -> Dict[str, Dict[str, float]]:
    by_sess: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_sess[t.session].append(t)

    result = {}
    for sess, t_list in by_sess.items():
        wins = sum(1 for t in t_list if t.pnl_net > 0)
        total_pnl = sum(t.pnl_net for t in t_list)
        result[sess] = {
            "count": len(t_list),
            "wins": wins,
            "losses": len(t_list) - wins,
            "win_rate": wins / len(t_list) * 100,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(t_list),
            "avg_holding_bars": sum(t.holding_bars for t in t_list) / len(t_list),
            "avg_mae": sum(t.mae for t in t_list) / len(t_list),
            "avg_mfe": sum(t.mfe for t in t_list) / len(t_list),
        }
    return result


def exit_reason_breakdown(trades: List[TradeRecord]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for t in trades:
        counts[t.exit_reason] += 1
    return dict(counts)


def build_metrics_summary(trades: List[TradeRecord],
                          spread_points: float,
                          slippage_points: float,
                          strategy: str,
                          mode: str,
                          start: str,
                          end: str) -> Dict:
    """Build a complete metrics dict for reporting."""
    n = len(trades)
    if n == 0:
        return {
            "strategy": strategy, "mode": mode,
            "start": start, "end": end,
            "total_trades": 0,
            "spread_points": spread_points,
            "slippage_points": slippage_points,
            "error": "No trades generated in date range",
        }

    pnls = [t.pnl_net for t in trades]
    return {
        "strategy": strategy,
        "mode": mode,
        "start": start,
        "end": end,
        "total_trades": n,
        "wins": sum(1 for t in trades if t.pnl_net > 0),
        "losses": sum(1 for t in trades if t.pnl_net < 0),
        "breakeven": sum(1 for t in trades if t.pnl_net == 0),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "expectancy": expectancy(trades),
        "avg_pnl": sum(pnls) / n,
        "total_pnl": sum(pnls),
        "sharpe_ratio": round(sharpe_ratio(trades), 2),
        "sortino_ratio": round(sortino_ratio(trades), 2),
        "max_drawdown": max_drawdown(trades),
        "avg_mae": max_adverse_excursion(trades),
        "avg_mfe": max_favorable_excursion(trades),
        "mae_p95": mae_percentile(trades, 95),
        "mfe_p95": mfe_percentile(trades, 95),
        "avg_holding_bars": sum(t.holding_bars for t in trades) / n,
        "max_holding_bars": max(t.holding_bars for t in trades),
        "spread_points": spread_points,
        "slippage_points": slippage_points,
        "total_spread_cost": sum(t.spread_cost for t in trades),
        "total_slippage_cost": sum(t.slippage_cost for t in trades),
        "total_costs": sum(t.total_cost for t in trades),
        "avg_cost_per_trade": sum(t.total_cost for t in trades) / n,
        "session_breakdown": session_breakdown(trades),
        "exit_reasons": exit_reason_breakdown(trades),
        "avg_confidence": sum(t.confidence for t in trades) / n,
        "avg_pnl_r": sum(t.pnl_r for t in trades) / n,
    }