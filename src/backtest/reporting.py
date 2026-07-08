"""reporting.py — CSV + Markdown report generation.

Manual-only; no broker / execution / cron / dashboard wiring.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .engine import TradeRecord
from .metrics import build_metrics_summary


def write_trades_csv(trades: List[TradeRecord], output_path: str) -> str:
    """Write trade-level CSV. Returns path written."""
    fieldnames = [
        "trade_id", "entry_time", "exit_time", "direction",
        "entry_price", "exit_price", "stop_loss", "take_profit",
        "pnl_gross", "pnl_net", "pnl_r",
        "session", "spread_cost", "slippage_cost", "total_cost",
        "mae", "mfe", "holding_bars", "exit_reason", "confidence",
    ]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for t in trades:
            w.writerow({
                "trade_id": t.trade_id,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "direction": "long" if t.direction == 1 else "short",
                "entry_price": round(t.entry_price, 4),
                "exit_price": round(t.exit_price, 4),
                "stop_loss": round(t.stop_loss, 4),
                "take_profit": round(t.take_profit, 4),
                "pnl_gross": round(t.pnl_gross, 4),
                "pnl_net": round(t.pnl_net, 4),
                "pnl_r": round(t.pnl_r, 4),
                "session": t.session,
                "spread_cost": round(t.spread_cost, 4),
                "slippage_cost": round(t.slippage_cost, 4),
                "total_cost": round(t.total_cost, 4),
                "mae": round(t.mae, 4),
                "mfe": round(t.mfe, 4),
                "holding_bars": t.holding_bars,
                "exit_reason": t.exit_reason,
                "confidence": round(t.confidence, 4),
            })
    return output_path


def _fmt_pnl(v: float) -> str:
    return f"${v:+.2f}"


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def format_markdown_report(metrics: Dict, trades: List[TradeRecord],
                           params: Optional[Dict] = None) -> str:
    """Build full Markdown report string from metrics dict."""
    lines = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M HKT")

    # Header
    lines.append(f"# Backtest Report — {metrics['strategy']}")
    lines.append(f"")
    lines.append(f"**Mode:** {metrics['mode']}  |  **Date:** {ts}  |  **Period:** {metrics['start']} → {metrics['end']}")
    lines.append(f"")
    lines.append(f"> ⚠️ *This is a backtest result, not financial advice. Past performance does not guarantee future results.*")
    lines.append(f"")

    # Parameter table
    if params:
        lines.append(f"### Strategy Parameters")
        lines.append(f"")
        lines.append(f"| Parameter | Value |")
        lines.append(f"|---|---|")
        for k, v in sorted(params.items()):
            lines.append(f"| {k} | {v} |")
        lines.append(f"")

    # Cost assumptions
    lines.append(f"### Cost Assumptions")
    lines.append(f"")
    lines.append(f"| Item | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Spread | {metrics['spread_points']} points (${metrics['spread_points'] * 0.10:.2f}/trade) |")
    lines.append(f"| Slippage | {metrics['slippage_points']} points (${metrics['slippage_points'] * 0.10:.2f}/trade) |")
    lines.append(f"| Avg cost / trade | ${metrics['avg_cost_per_trade']:.2f} |")
    lines.append(f"| Total costs | ${metrics['total_costs']:.2f} |")
    lines.append(f"")

    # Summary metrics
    n = metrics["total_trades"]
    lines.append(f"### Summary Metrics")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Total Trades | {n} |")
    lines.append(f"| Wins / Losses | {metrics['wins']} / {metrics['losses']} |")
    lines.append(f"| Win Rate | {_fmt_pct(metrics['win_rate'])} |")
    lines.append(f"| Total P&L | {_fmt_pnl(metrics['total_pnl'])} |")
    lines.append(f"| Avg P&L / trade | {_fmt_pnl(metrics['avg_pnl'])} |")
    lines.append(f"| Profit Factor | {metrics['profit_factor']:.2f} |")
    lines.append(f"| Expectancy | {_fmt_pnl(metrics['expectancy'])}/trade |")
    lines.append(f"| Avg R-multiple | {metrics['avg_pnl_r']:.2f}R |")
    lines.append(f"| Sharpe Ratio | {metrics['sharpe_ratio']:.2f} |")
    lines.append(f"| Sortino Ratio | {metrics['sortino_ratio']:.2f} |")
    lines.append(f"| Max Drawdown | ${metrics['max_drawdown']:.2f} |")
    lines.append(f"| Avg Confidence | {metrics['avg_confidence'] * 100:.0f}% |")
    lines.append(f"")

    # MAE / MFE
    lines.append(f"### MAE / MFE (Adverse / Favorable Excursion)")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Avg MAE | {metrics['avg_mae']:.2f} pts |")
    lines.append(f"| P95 MAE | {metrics['mae_p95']:.2f} pts |")
    lines.append(f"| Avg MFE | {metrics['avg_mfe']:.2f} pts |")
    lines.append(f"| P95 MFE | {metrics['mfe_p95']:.2f} pts |")
    lines.append(f"| MFE / MAE ratio | {metrics['avg_mfe'] / max(metrics['avg_mae'], 0.01):.2f} |")
    lines.append(f"")

    # Holding time
    lines.append(f"### Holding Time")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Avg bars held | {metrics['avg_holding_bars']:.1f} |")
    lines.append(f"| Max bars held | {metrics['max_holding_bars']} |")
    lines.append(f"")

    # Exit reasons
    if metrics.get("exit_reasons"):
        lines.append(f"### Exit Reasons")
        lines.append(f"")
        er = metrics["exit_reasons"]
        for reason, count in sorted(er.items(), key=lambda x: -x[1]):
            lines.append(f"- **{reason.upper()}**: {count} ({count / n * 100:.0f}%)")
        lines.append(f"")

    # Session breakdown
    sb = metrics.get("session_breakdown", {})
    if sb:
        lines.append(f"### Session Breakdown")
        lines.append(f"")
        lines.append(f"| Session | Count | Win Rate | Avg P&L | Avg MAE | Avg MFE |")
        lines.append(f"|---|---|---|---|---|---|")
        for sess, sdata in sorted(sb.items()):
            lines.append(
                f"| {sess} | {sdata['count']} | {_fmt_pct(sdata['win_rate'])} | "
                f"{_fmt_pnl(sdata['avg_pnl'])} | {sdata['avg_mae']:.2f} | {sdata['avg_mfe']:.2f} |"
            )
        lines.append(f"")

    # Trade list (abbreviated)
    if trades:
        lines.append(f"### Trade Log ({len(trades)} trades)")
        lines.append(f"")
        lines.append(f"| # | Entry | Exit | Dir | P&L | P&L R | MAE | MFE | Bars | Exit |")
        lines.append(f"|---|---|---|---|---|---|---|---|---|---|")
        for t in trades[:50]:  # cap at 50 for readability
            direction = "L" if t.direction == 1 else "S"
            lines.append(
                f"| {t.trade_id} | {t.entry_time[5:16]} | {t.exit_time[5:16]} | "
                f"{direction} | {_fmt_pnl(t.pnl_net)} | {t.pnl_r:.2f}R | "
                f"{t.mae:.2f} | {t.mfe:.2f} | {t.holding_bars} | {t.exit_reason} |"
            )
        if len(trades) > 50:
            lines.append(f"| ... (+{len(trades) - 50} more trades) | | | | | | | | | |")
        lines.append(f"")

    lines.append(f"*Report generated: {ts}*")

    return "\n".join(lines)


def generate_reports(trades: List[TradeRecord],
                     metrics: Dict,
                     params: Dict,
                     output_dir_csv: str,
                     output_dir_md: str,
                     strategy: str,
                     mode: str,
                     output_format: str) -> Tuple[Optional[str], Optional[str]]:
    """Generate CSV and/or Markdown reports. Returns (csv_path, md_path)."""
    import os
    from datetime import datetime as dt

    date_str = dt.now().strftime("%Y%m%d_%H%M%S")
    safe_name = strategy.replace("_", "-")
    csv_path = None
    md_path = None

    if output_format in ("csv", "both"):
        csv_name = f"{date_str}_{safe_name}_{mode}.csv"
        csv_full = os.path.join(output_dir_csv, csv_name)
        write_trades_csv(trades, csv_full)
        csv_path = csv_full

    if output_format in ("markdown", "both"):
        md_name = f"{date_str}_{safe_name}_{mode}.md"
        md_full = os.path.join(output_dir_md, md_name)
        report_text = format_markdown_report(metrics, trades, params)
        Path(md_full).parent.mkdir(parents=True, exist_ok=True)
        with open(md_full, "w", encoding="utf-8") as f:
            f.write(report_text)
        md_path = md_full

    return csv_path, md_path