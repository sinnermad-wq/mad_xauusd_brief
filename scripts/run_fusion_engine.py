#!/usr/bin/env python3
"""run_fusion_engine.py — CLI entry point for Fusion Engine v1.

Usage:
  # Auto: load latest briefing + candle, run fusion
  python scripts/run_fusion_engine.py --output text

  # From specific files
  python scripts/run_fusion_engine.py \
      --briefing data/xauusd_refresh/pre_ny/20260708_T0800_refresh.json \
      --candle   data/candle_engine/m5/20260708_T0755_candle_engine.json \
      --output both

  # From latest M5 candle only
  python scripts/run_fusion_engine.py --candle-only --output text

  # Briefing only (reduced quality)
  python scripts/run_fusion_engine.py --briefing-only --output text

  # Dry-run
  python scripts/run_fusion_engine.py --dry-run

Manual-only: no broker / execution / auto-trade.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HKT = timezone(timedelta(hours=8))
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fusion.engine import FusionEngine, BriefingInput, CandleInput
from fusion.output import (
    SCHEMA_VERSION,
    build_output,
    write_json,
    write_csv,
    write_markdown,
    format_text_summary,
)

DEFAULT_OUTPUT = _REPO_ROOT / "data" / "fusion"


def parse_args():
    p = argparse.ArgumentParser(description="Fusion Engine v1")
    p.add_argument("--briefing", help="Path to briefing/refresh JSON")
    p.add_argument("--candle", help="Path to candlestick engine JSON")
    p.add_argument("--briefing-only", action="store_true",
                   help="Run fusion with briefing only (reduced quality)")
    p.add_argument("--candle-only", action="store_true",
                   help="Run fusion with candlestick only (reduced quality)")
    p.add_argument("--input-dir-briefing",
                   default=str(_REPO_ROOT / "data" / "xauusd_refresh"),
                   help="Dir to auto-find latest briefing JSON")
    p.add_argument("--input-dir-candle",
                   default=str(_REPO_ROOT / "data" / "candle_engine"),
                   help="Dir to auto-find latest candle JSON")
    p.add_argument("--timeframe", default="M5",
                   help="Timeframe for candle lookup (default: M5)")
    p.add_argument("--output", default="text",
                   choices=["json", "csv", "md", "both", "text"],
                   help="Output format (default: text)")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT),
                   help=f"Output directory (default: {DEFAULT_OUTPUT})")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute but don't write files")
    return p.parse_args()


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest_json(directory: str, pattern: str = "*.json") -> Optional[str]:
    """Return most recent JSON file in directory tree."""
    base = Path(directory)
    if not base.exists():
        return None
    files = sorted(base.rglob(pattern), key=lambda p: p.stat().st_mtime)
    return str(files[-1]) if files else None


def load_briefing(path: Optional[str], input_dir: str, candle_only: bool,
                  briefing_only: bool) -> Optional[BriefingInput]:
    if briefing_only:
        # Synthetic briefing from candle-only mode
        return None

    if not path:
        # Auto-find latest
        candidates = [
            f"{input_dir}/morning/*.json",
            f"{input_dir}/pre_london/*.json",
            f"{input_dir}/pre_ny/*.json",
        ]
        for pattern in candidates:
            found = find_latest_json(pattern.rsplit("/", 1)[0], "*.json")
            if found:
                path = found
                break

    if not path:
        return None

    try:
        d = load_json(path)
        return BriefingInput.from_dict(d)
    except Exception as e:
        print(f"Warning: could not load briefing from {path}: {e}")
        return None


def load_candle(path: Optional[str], input_dir: str, timeframe: str,
                briefing_only: bool) -> Optional[CandleInput]:
    if briefing_only:
        return None

    if not path:
        tf_dir = input_dir / timeframe.lower()
        path = find_latest_json(str(tf_dir), "*.json")

    if not path:
        return None

    try:
        d = load_json(path)
        return CandleInput.from_dict(d)
    except Exception as e:
        print(f"Warning: could not load candle from {path}: {e}")
        return None


def main():
    args = parse_args()
    out_dir = Path(args.output_dir)

    print(f"[{datetime.now(HKT).strftime('%H:%M:%S')}] "
          f"XAUUSD Fusion Engine v{SCHEMA_VERSION}")

    briefing_dir = Path(args.input_dir_briefing)
    candle_dir   = Path(args.input_dir_candle)

    # Load inputs
    b_input = load_briefing(
        args.briefing, str(briefing_dir),
        args.candle_only, args.briefing_only,
    )
    c_input = load_candle(
        args.candle, candle_dir, args.timeframe,
        args.briefing_only,
    )

    if b_input:
        print(f"Briefing: {b_input.job_name or b_input.job_type or 'loaded'} "
              f"({b_input.timestamp_hkt or 'no timestamp'})")
    else:
        print("Briefing: NOT LOADED")

    if c_input:
        print(f"Candle: {c_input.timeframe} close={c_input.close} "
              f"bias={c_input.direction_bias:+.2f} ({c_input.primary_state})")
    else:
        print("Candle: NOT LOADED")

    if not b_input and not c_input:
        print("ERROR: No inputs available. Provide --briefing and/or ensure "
              "data/candle_engine/m5/ has JSON files.")
        sys.exit(2)

    # Run fusion
    engine = FusionEngine()
    result = engine.run(briefing=b_input, candle=c_input)

    # Build output dict
    output = build_output(
        decision=result["decision"],
        decision_strength=result["decision_strength"],
        context_score=result["context_score"],
        price_action_score=result["price_action_score"],
        environment_score=result["environment_score"],
        quality_score=result["quality_score"],
        confluence_score=result["confluence_score"],
        directional_bias=result["directional_bias"],
        bias_strength=result["bias_strength"],
        market_regime=result["market_regime"],
        risk_state=result["risk_state"],
        entry_readiness=result["entry_readiness"],
        reasons=result["reasons"],
        conflicts=result["conflicts"],
        warnings=result["warnings"],
        inputs_used=result["inputs_used"],
        missing_inputs=result["missing_inputs"],
        extra={
            "_briefing_timestamp":   result["_briefing_timestamp"],
            "_candle_timestamp":     result["_candle_timestamp"],
            "_candle_close":         result["_candle_close"],
            "_candle_direction_bias": result["_candle_direction_bias"],
            "_candle_primary_state": result["_candle_primary_state"],
        },
    )

    # ── Output ────────────────────────────────────────────────────────────
    if args.output == "text":
        print()
        print(format_text_summary(output))

    if args.dry_run:
        print("\n[DRY RUN] Files not written.")
        return

    mode_dir = out_dir
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