"""output.py — JSON/CSV/MD output for candlestick engine results.

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
HKT = timezone(timedelta(hours=8))


def _hkt_now() -> datetime:
    return datetime.now(HKT)


def _to_native(obj):
    """Convert numpy / pandas types to native Python for JSON serialization."""
    if hasattr(obj, "item"):    # numpy scalar
        return obj.item()
    if hasattr(obj, "tolist"):  # numpy array / Series
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(x) for x in obj]
    return obj


def build_output(
    symbol: str,
    timeframe: str,
    close: float,
    direction_bias: float,
    primary_state: str,
    momentum_state: str,
    rejection_state: str,
    range_state: str,
    structure_state: str,
    sequence_state: str,
    pattern_tags: List[str],
    momentum_score: float,
    rejection_score: float,
    compression_score: float,
    structure_score: float,
    confidence_score: float,
    context_tags: List[str],
    warnings: List[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the unified output dict."""
    now = _hkt_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "generated_at": now.isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "close": round(close, 2),
        "direction_bias": direction_bias,   # -1 to +1
        "primary_state": primary_state,
        "momentum_state": momentum_state,
        "rejection_state": rejection_state,
        "range_state": range_state,
        "structure_state": structure_state,
        "sequence_state": sequence_state,
        "pattern_tags": pattern_tags,
        "momentum_score": momentum_score,
        "rejection_score": rejection_score,
        "compression_score": compression_score,
        "structure_score": structure_score,
        "confidence_score": confidence_score,
        "context_tags": context_tags,
        "warnings": warnings,
        **(extra or {}),
    }


def write_json(output: Dict[str, Any], out_dir: Path) -> Path:
    """Write JSON output."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _hkt_now().strftime("%Y%m%d_T%H%M")
    fpath = out_dir / f"{ts}_candle_engine.json"
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(_to_native(output), f, indent=2, ensure_ascii=False)
    return fpath


def write_csv(output: Dict[str, Any], out_dir: Path) -> Path:
    """Write single-row CSV (append-friendly)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _hkt_now().strftime("%Y%m%d_T%H%M")
    fpath = out_dir / f"{ts}_candle_engine.csv"

    flat = {
        "timestamp": output["timestamp"],
        "symbol": output["symbol"],
        "timeframe": output["timeframe"],
        "close": output["close"],
        "direction_bias": output["direction_bias"],
        "primary_state": output["primary_state"],
        "momentum_state": output["momentum_state"],
        "rejection_state": output["rejection_state"],
        "range_state": output["range_state"],
        "structure_state": output["structure_state"],
        "sequence_state": output["sequence_state"],
        "pattern_tags": "|".join(output["pattern_tags"]),
        "momentum_score": output["momentum_score"],
        "rejection_score": output["rejection_score"],
        "compression_score": output["compression_score"],
        "structure_score": output["structure_score"],
        "confidence_score": output["confidence_score"],
        "context_tags": "|".join(output["context_tags"]),
        "warnings": "|".join(output["warnings"]),
    }

    # Append to CSV (create if not exists)
    fieldnames = list(flat.keys())
    import os
    file_exists = os.path.exists(fpath)
    with open(fpath, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        w.writerow(flat)
    return fpath


def write_markdown(output: Dict[str, Any], out_dir: Path) -> Path:
    """Write human-readable markdown report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _hkt_now().strftime("%Y%m%d_T%H%M")
    fpath = out_dir / f"{ts}_candle_engine.md"

    bias = output["direction_bias"]
    bias_str = f"{bias:+.3f}"
    bias_bar = "🟢" + "▓" * int(abs(bias) * 10) + "░" * (10 - int(abs(bias) * 10)) if bias >= 0 else "🔴" + "▓" * int(abs(bias) * 10) + "░" * (10 - int(abs(bias) * 10))

    lines = [
        f"# Candlestick Direction Engine Report",
        f"",
        f"**{output['timestamp'][:10]}** {output['timeframe']} | **{output['symbol']}** {output['close']}",
        f"",
        f"**Bias:** {bias_str} {bias_bar}",
        f"**Confidence:** {output['confidence_score']:.0f}%",
        f"",
        f"## States",
        f"",
        f"| State | Value |",
        f"|---|---|",
        f"| Direction | {output['direction_bias']:+.3f} |",
        f"| Momentum | {output['momentum_state']} ({output['momentum_score']:.0f}) |",
        f"| Rejection | {output['rejection_state']} ({output['rejection_score']:.0f}) |",
        f"| Range | {output['range_state']} ({output['compression_score']:.0f}) |",
        f"| Structure | {output['structure_state']} ({output['structure_score']:.0f}) |",
        f"| Sequence | {output['sequence_state']} |",
        f"| Primary | {output['primary_state']} |",
        f"| Secondary | {output.get('secondary_states', [])} |",
        f"",
    ]

    if output["pattern_tags"]:
        lines += [
            f"## Patterns Detected",
            f"",
            *(f"- `{t}`" for t in output["pattern_tags"]),
            f"",
        ]

    if output["context_tags"]:
        lines += [
            f"## Context",
            f"",
            *(f"- `{t}`" for t in output["context_tags"]),
            f"",
        ]

    if output["warnings"]:
        lines += [
            f"## Warnings",
            f"",
            *(f"- ⚠️ {w}" for w in output["warnings"]),
            f"",
        ]

    lines += [
        f"---",
        f"*Generated: {output['generated_at']} | Schema v{SCHEMA_VERSION}*",
    ]

    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fpath


def format_text_summary(output: Dict[str, Any]) -> str:
    """Build Telegram-friendly text summary."""
    bias = output["direction_bias"]
    bias_str = f"{bias:+.3f}"
    if bias >= 0.3:
        bias_emoji = "🟢 BULLISH"
    elif bias <= -0.3:
        bias_emoji = "🔴 BEARISH"
    else:
        bias_emoji = "⚪ NEUTRAL"

    lines = [
        f"📊 *XAUUSD {output['timeframe']} Candle Engine*",
        f"",
        f"Close: *{output['close']}* | Bias: *{bias_str}* {bias_emoji}",
        f"Confidence: {output['confidence_score']:.0f}%",
        f"",
        f"Primary: `{output['primary_state']}` | Momentum: `{output['momentum_state']}`",
        f"Structure: `{output['structure_state']}` | Range: `{output['range_state']}`",
        f"Sequence: `{output['sequence_state']}` | Rejection: `{output['rejection_state']}`",
    ]

    if output["pattern_tags"]:
        lines.append(f"Patterns: {', '.join(output['pattern_tags'][:4])}")

    if output["warnings"]:
        for w in output["warnings"][:2]:
            lines.append(f"⚠️ {w}")

    return "\n".join(lines)