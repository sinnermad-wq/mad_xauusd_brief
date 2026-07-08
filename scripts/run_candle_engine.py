#!/usr/bin/env python3
"""run_candle_engine.py — CLI entry point for Candlestick Direction Engine v1.

Usage:
  # M1 live analysis (yfinance)
  python scripts/run_candle_engine.py --symbol GC=F --timeframe M1 --output json

  # M5 live analysis
  python scripts/run_candle_engine.py --symbol GC=F --timeframe M5 --output both

  # From saved CSV
  python scripts/run_candle_engine.py --input data/sample_m1.csv --output both

  # Dry-run (preview only)
  python scripts/run_candle_engine.py --symbol GC=F --timeframe M1 --dry-run

  # Show text summary only
  python scripts/run_candle_engine.py --symbol GC=F --timeframe M1 --format text

Manual-only: no broker / execution / Telegram auto-signal.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Resolve package
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from candlestick.engine import CandlestickEngine, EngineConfig, fetch_bars, HKT
from candlestick.output import (
    SCHEMA_VERSION,
    build_output,
    write_json,
    write_csv,
    write_markdown,
    format_text_summary,
)

OUTPUT_DIR = _REPO_ROOT / "data" / "candle_engine"
DEFAULT_SYMBOL = "GC=F"


def parse_args():
    p = argparse.ArgumentParser(description="Candlestick Direction Engine v1")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Ticker symbol (default: GC=F)")
    p.add_argument("--timeframe", default="M1", choices=["M1", "M5", "M15", "H1"],
                   help="Timeframe (default: M1)")
    p.add_argument("--period", default="5d",
                   help="yfinance period for M1/M5 (default: 5d)")
    p.add_argument("--lookback", type=int, default=50,
                   help="Bars to analyze (default: 50)")
    p.add_argument("--input", help="Load from local CSV instead of yfinance")
    p.add_argument("--output", default="text",
                   choices=["json", "csv", "md", "both", "text"],
                   help="Output format (default: text)")
    p.add_argument("--output-dir", default=str(OUTPUT_DIR),
                   help=f"Output directory (default: {OUTPUT_DIR})")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch and compute, but don't write files")
    p.add_argument("--format", dest="fmt2", default="text",
                   choices=["text", "compact"],
                   help="Text format variant")
    p.add_argument("--ema-fast", type=int, default=8,
                   help="Fast EMA period (default: 8)")
    p.add_argument("--ema-slow", type=int, default=21,
                   help="Slow EMA period (default: 21)")
    return p.parse_args()


def load_csv(path: str) -> "pd.DataFrame":
    import pandas as pd
    df = pd.read_csv(path)
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0
    return df


_INTERVAL_MAP = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}

def main():
    args = parse_args()
    out_dir = Path(args.output_dir)

    print(f"[{datetime.now(HKT).strftime('%H:%M:%S')}] "
          f"XAUUSD {args.timeframe} Candlestick Engine v{SCHEMA_VERSION}")

    # Load data
    if args.input:
        df = load_csv(args.input)
        print(f"Loaded {len(df)} rows from {args.input}")
    else:
        interval = _INTERVAL_MAP.get(args.timeframe, args.timeframe.lower())
        fetch_period = args.period if args.timeframe != "H1" else "60d"
        print(f"Fetching {args.symbol} {args.timeframe} from yfinance (period={fetch_period})...")
        try:
            df = fetch_bars(args.symbol, period=fetch_period, interval=interval)
        except Exception as e:
            print(f"ERROR: {e}")
            sys.exit(2)
        print(f"Loaded {len(df)} bars: {df.index[0]} → {df.index[-1]}")

    if len(df) < 10:
        print("ERROR: insufficient bars (< 10)")
        sys.exit(2)

    # Run engine
    cfg = EngineConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        lookback=args.lookback,
        ema_fast_period=args.ema_fast,
        ema_slow_period=args.ema_slow,
    )
    engine = CandlestickEngine(cfg)
    result = engine.run(df)

    # Build output dict
    extra = {}
    if result.latest_features:
        extra = {"_features": result.latest_features}
    if hasattr(result, "scores") and result.scores:
        extra["_scores"] = {
            "secondary_states": result.scores.secondary_states,
        }

    output = build_output(
        symbol=result.symbol,
        timeframe=result.timeframe,
        close=result.close,
        direction_bias=result.direction_bias,
        primary_state=result.primary_state,
        momentum_state=result.momentum_state,
        rejection_state=result.rejection_state,
        range_state=result.range_state,
        structure_state=result.structure_state,
        sequence_state=result.sequence_state,
        pattern_tags=result.pattern_tags,
        momentum_score=result.momentum_score,
        rejection_score=result.rejection_score,
        compression_score=result.compression_score,
        structure_score=result.structure_score,
        confidence_score=result.confidence_score,
        context_tags=result.context_tags,
        warnings=result.warnings,
        extra=extra,
    )

    # ── Output ──────────────────────────────────────────────────────────────
    if args.output == "text" or args.output == "text":
        if args.fmt2 == "compact":
            bias = result.direction_bias
            print(f"XAUUSD {result.close} | bias={bias:+.3f} | "
                  f"primary={result.primary_state} | "
                  f"conf={result.confidence_score:.0f}%")
        else:
            print(format_text_summary(output))
            if result.warnings:
                print("\n⚠️ Warnings:")
                for w in result.warnings:
                    print(f"  - {w}")

    if args.dry_run:
        print("\n[DRY RUN] Files not written.")
        return

    mode = args.timeframe.lower()
    mode_dir = out_dir / mode
    mode_dir.mkdir(parents=True, exist_ok=True)

    if args.output in ("json", "both"):
        p = write_json(output, mode_dir)
        print(f"JSON: {p}")

    if args.output in ("csv", "both"):
        p = write_csv(output, mode_dir)
        print(f"CSV: {p}")

    if args.output in ("md", "both"):
        p = write_markdown(output, mode_dir)
        print(f"MD: {p}")


if __name__ == "__main__":
    main()