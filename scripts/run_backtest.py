#!/usr/bin/env python3
"""run_backtest.py — CLI entry point for Candlestick Direction Engine backtest research.

Usage:
  python scripts/run_backtest.py --strategy baseline_ma --mode general \\
      --start 2025-01-01 --end 2025-12-31 --format both

Manual-only: no broker / execution / cron / dashboard wiring.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap: add src/ to path
_root = Path(__file__).resolve().parent.parent / "src"
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pandas as pd
import yfinance as yf

from backtest.engine import BacktestConfig, BacktestEngine
from backtest.metrics import build_metrics_summary
from backtest.reporting import generate_reports
from backtest.strategies import (
    STRATEGY_MODES,
    STRATEGY_DEFAULTS,
    apply_strategy,
    detect_session,
)


DEFAULT_OUTPUT_DIR_CSV = "data/backtests"
DEFAULT_OUTPUT_DIR_MD = "reports/backtests"


def parse_args():
    p = argparse.ArgumentParser(description="XAUUSD Backtest Research CLI")
    p.add_argument("--mode", required=True, choices=["general", "scalp"],
                   help="general or scalp strategy mode")
    p.add_argument("--strategy", required=True,
                   help="Strategy name (see strategies.py)")
    p.add_argument("--symbol", default="GC=F",
                   help="yfinance ticker symbol (default GC=F gold futures)")
    p.add_argument("--timeframe", default="1h",
                   help="Chart timeframe (default 1h)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--input", help="Optional local CSV/JSONL to use instead of yfinance")
    p.add_argument("--output-dir", default=None,
                   help=f"Output dir for both CSV and MD (default: {DEFAULT_OUTPUT_DIR_CSV}/ + {DEFAULT_OUTPUT_DIR_MD}/)")
    p.add_argument("--output-format", dest="output_format", default="both",
                   choices=["csv", "markdown", "both"],
                   help="Output format (default both)")
    p.add_argument("--session", dest="session_filter",
                   help="Session filter: asian / london / new_york / overlap")
    p.add_argument("--spread-points", type=float, default=30.0,
                   help="Spread in XAUUSD points (default 30 = $3.00/trade)")
    p.add_argument("--slippage-points", type=float, default=10.0,
                   help="Slippage in XAUUSD points (default 10 = $1.00/trade)")
    p.add_argument("--params", help='JSON string of strategy params, e.g. \'{"fast":20,"slow":50}\'')
    p.add_argument("--dry-run", action="store_true",
                   help="Load data and print strategy signals but do not run backtest")
    p.add_argument("--show-params", action="store_true",
                   help="Print strategy defaults and exit")
    return p.parse_args()


def load_data(symbol: str, start: str, end: str, timeframe: str,
              input_path: str = None) -> pd.DataFrame:
    """Load OHLCV data from local file or yfinance."""
    if input_path and os.path.exists(input_path):
        ext = os.path.splitext(input_path)[1].lower()
        if ext in (".csv", ".jsonl"):
            # Try CSV first
            try:
                df = pd.read_csv(input_path, parse_dates=["datetime"])
                df.set_index("datetime", inplace=True)
                return df
            except Exception:
                pass
            # Try JSONL (one JSON object per line, yfinance-style)
            records = []
            with open(input_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    records.append(json.loads(line))
            df = pd.DataFrame(records)
            if "Datetime" in df.columns:
                df["Datetime"] = pd.to_datetime(df["Datetime"])
                df.set_index("Datetime", inplace=True)
            return df
        raise ValueError(f"Unsupported input format: {ext}")

    # Fetch from yfinance
    print(f"Fetching {symbol} {timeframe} from {start} to {end} via yfinance...")
    try:
        ticker = yf.Ticker(symbol)
        # For intraday we need to specify interval
        if timeframe != "1d":
            df = ticker.history(start=start, end=end, interval=timeframe, auto_adjust=True)
        else:
            df = ticker.history(start=start, end=end, auto_adjust=True)
        if df.empty:
            raise ValueError("yfinance returned empty DataFrame")
        # Standardise column names
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"ERROR loading data: {e}")
        print("Try providing --input with a local CSV file.")
        sys.exit(2)


def parse_params(params_str: str) -> dict:
    """Parse --params JSON string."""
    if not params_str:
        return {}
    try:
        return json.loads(params_str)
    except json.JSONDecodeError as e:
        print(f"ERROR: --params must be valid JSON: {e}")
        sys.exit(1)


def main():
    args = parse_args()

    # Show defaults
    if args.show_params:
        for s, defaults in STRATEGY_DEFAULTS.items():
            mode = STRATEGY_MODES.get(s, "unknown")
            print(f"  {s} ({mode}): {defaults}")
        return

    # Validate strategy matches mode
    expected_mode = STRATEGY_MODES.get(args.strategy)
    if expected_mode is None:
        print(f"ERROR: Unknown strategy '{args.strategy}'")
        print(f"Available: {list(STRATEGY_MODES.keys())}")
        sys.exit(1)
    if expected_mode != args.mode:
        print(f"ERROR: Strategy '{args.strategy}' is a {expected_mode} strategy, "
              f"but --mode is {args.mode}. Use --mode {expected_mode}.")
        sys.exit(1)

    # Merge strategy defaults with overrides
    defaults = STRATEGY_DEFAULTS.get(args.strategy, {})
    overrides = parse_params(args.params)
    params = {**defaults, **overrides}

    # Load data
    df = load_data(args.symbol, args.start, args.end, args.timeframe, args.input)

    # Add session label
    df["session"] = detect_session(df.index)

    print(f"Loaded {len(df)} bars, {df.index[0]} to {df.index[-1]}")

    # Generate signals
    signals = apply_strategy(df, args.strategy, params)
    print(f"Generated {len(signals)} signals")

    if args.dry_run:
        print("\n[DRY RUN] Backtest not executed. Signals preview:")
        for s in signals[:10]:
            print(f"  {s.timestamp} | {s.direction} | {s.entry_price:.4f} "
                  f"| SL:{s.stop_loss:.4f} TP:{s.take_profit:.4f} "
                  f"| {s.session} | {s.reason}")
        if len(signals) > 10:
            print(f"  ... ({len(signals) - 10} more signals)")
        return

    # Run backtest
    config = BacktestConfig(
        spread_points=args.spread_points,
        slippage_points=args.slippage_points,
    )
    engine = BacktestEngine(config)
    trades = engine.run(df, signals, session_filter=args.session_filter)
    print(f"Executed {len(trades)} trades")

    if not trades:
        print("WARNING: No trades generated. Check date range and strategy parameters.")
        return

    # Build metrics
    metrics = build_metrics_summary(
        trades, args.spread_points, args.slippage_points,
        args.strategy, args.mode, args.start, args.end,
    )

    # Print summary to stdout
    print("\n=== Backtest Summary ===")
    print(f"Strategy: {args.strategy} ({args.mode})")
    print(f"Period: {args.start} -> {args.end}")
    print(f"Trades: {metrics['total_trades']} | "
          f"W: {metrics['wins']} L: {metrics['losses']} | "
          f"WR: {metrics['win_rate']:.1f}%")
    print(f"Total P&L: ${metrics['total_pnl']:.2f} | "
          f"Avg: ${metrics['avg_pnl']:.2f}/trade")
    print(f"Profit Factor: {metrics['profit_factor']:.2f} | "
          f"Expectancy: ${metrics['expectancy']:.2f}/trade")
    print(f"Sharpe: {metrics['sharpe_ratio']:.2f} | "
          f"Sortino: {metrics['sortino_ratio']:.2f}")
    print(f"Max Drawdown: ${metrics['max_drawdown']:.2f}")
    print(f"MAE avg: {metrics['avg_mae']:.2f} pts | MFE avg: {metrics['avg_mfe']:.2f} pts")
    print(f"Total costs: ${metrics['total_costs']:.2f} | "
          f"Avg cost/trade: ${metrics['avg_cost_per_trade']:.2f}")

    # Generate reports
    out_base = args.output_dir or "."
    csv_dir = os.path.join(out_base, DEFAULT_OUTPUT_DIR_CSV.lstrip("./")) if args.output_dir else DEFAULT_OUTPUT_DIR_CSV
    md_dir = os.path.join(out_base, DEFAULT_OUTPUT_DIR_MD.lstrip("./")) if args.output_dir else DEFAULT_OUTPUT_DIR_MD

    # Use same base for both unless separate
    if args.output_format in ("csv", "both"):
        csv_dir = args.output_dir or DEFAULT_OUTPUT_DIR_CSV
    if args.output_format in ("markdown", "both"):
        md_dir = args.output_dir or DEFAULT_OUTPUT_DIR_MD

    csv_path, md_path = generate_reports(
        trades, metrics, params,
        output_dir_csv=csv_dir,
        output_dir_md=md_dir,
        strategy=args.strategy,
        mode=args.mode,
        output_format=args.output_format,
    )

    if csv_path:
        print(f"\nCSV: {csv_path}")
    if md_path:
        print(f"Markdown: {md_path}")

    # Also print session breakdown
    sb = metrics.get("session_breakdown", {})
    if sb:
        print("\nSession Breakdown:")
        for sess, sdata in sorted(sb.items()):
            print(f"  {sess}: count={sdata['count']}, "
                  f"WR={sdata['win_rate']:.0f}%, "
                  f"avg_pnl=${sdata['avg_pnl']:.2f}")


if __name__ == "__main__":
    main()