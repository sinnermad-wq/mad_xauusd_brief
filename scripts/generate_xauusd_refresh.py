#!/usr/bin/env python3
"""generate_xauusd_refresh.py — XAUUSD Briefing Refresh Schedule v1.

Generates structured refresh briefings at 3 fixed HKT times:
  morning        08:15 HKT  — overnight + open setup + event watch
  pre_london    14:45 HKT  — asia summary + london risk + updated bias
  pre_ny         20:15 HKT — london summary + us event risk + ny expectation

Outputs (always written to disk; Telegram dispatched separately):
  data/xauusd_refresh/<mode>/<date>T<time>_refresh.json  — machine-readable
  data/xauusd_refresh/<mode>/<date>T<time>_refresh.md   — readable log

Telegram is attempted AFTER the JSON is safely on disk.

Manual-only: no cron/daemon in this script. Cron setup is done separately.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── schema version ───────────────────────────────────────────────────────────
SCHEMA_VERSION = "1.0"

# ── timezone helpers ─────────────────────────────────────────────────────────
HKT = timezone(timedelta(hours=8))

def now_hkt() -> datetime:
    return datetime.now(HKT)

def today_hkt() -> date:
    return now_hkt().date()

def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Saturday=5, Sunday=6

# ── Telegram dispatch (best-effort, after file write) ────────────────────────
TELEGRAM_HOME = "telegram:980366696"  # 阿懶 home channel


def _hkt_offset(d: date, hour: int, minute: int = 0) -> datetime:
    """Return datetime in HKT for given date + HKT time."""
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=HKT)


def _fetch_gold_overnight(symbol: str = "GC=F", hkt_now: datetime = None) -> Dict[str, Any]:
    """Fetch recent bars via yfinance for analysis window."""
    if hkt_now is None:
        hkt_now = now_hkt()
    # Need enough bars for MA20/MA50/ATR
    start = (hkt_now - timedelta(days=5)).strftime("%Y-%m-%d")
    end = hkt_now.strftime("%Y-%m-%d")
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, auto_adjust=True)
        if df.empty:
            return {"error": "no data from yfinance", "bars": []}
        df.columns = [c.lower() for c in df.columns]
        return {"bars": df.reset_index().to_dict("records"), "error": None}
    except Exception as e:
        return {"error": str(e), "bars": []}


def _detect_market_status(hkt_now: datetime) -> str:
    """Return market open/closed/holiday status based on HKT time."""
    weekday = hkt_now.weekday()
    if weekday >= 5:
        return "closed_weekend"
    h = hkt_now.hour + hkt_now.minute / 60
    # Gold: roughly 07:00 HKT Asia start, 22:00 HKT London close (daylight)
    if 7.0 <= h < 22.0:
        return "open"
    else:
        return "closed_after_hours"


def _detect_volatility_regime(bars: List[Dict], period: int = 20) -> str:
    """ATR-based volatility regime: low / normal / high."""
    if len(bars) < period + 1:
        return "unknown"
    closes = [b["close"] for b in bars[-period - 1:]]
    highs = [b["high"] for b in bars[-period:]]
    lows = [b["low"] for b in bars[-period:]]
    import numpy as np
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = highs[i - 1], lows[i - 1], closes[i - 1]
        c = closes[i]
        tr = max(h - l, abs(h - c), abs(l - c))
        trs.append(tr)
    atr = np.mean(trs[-period:])
    recent_range = np.mean([highs[i] - lows[i] for i in range(-period, 0)])
    if atr < 0.005 * np.mean(closes):
        return "low"
    elif atr > 0.015 * np.mean(closes):
        return "high"
    return "normal"


def _detect_market_bias(bars: List[Dict], fast: int = 20, slow: int = 50) -> str:
    """Simple MA-based bias: bullish / bearish / neutral."""
    if len(bars) < slow:
        return "unknown"
    closes = [b["close"] for b in bars[-slow:]]
    import numpy as np
    ma_fast = np.mean(closes[-fast:])
    ma_slow = np.mean(closes[-slow:])
    price = closes[-1]
    if price > ma_fast * 1.002:
        return "bullish"
    elif price < ma_fast * 0.998:
        return "bearish"
    return "neutral"


def _compute_key_levels(bars: List[Dict], lookback: int = 20) -> Dict[str, float]:
    """Compute pivot S1/S2/R1/R2 + recent H/L."""
    if len(bars) < lookback + 1:
        return {}
    import numpy as np
    recent = bars[-lookback:]
    highs = [b["high"] for b in recent]
    lows = [b["low"] for b in recent]
    closes = [b["close"] for b in recent]
    h, l, c = highs[-1], lows[-1], closes[-1]
    pivot = (h + l + c) / 3
    return {
        "pivot": round(pivot, 2),
        "r1": round(2 * pivot - l, 2),
        "r2": round(pivot + (h - l), 2),
        "s1": round(2 * pivot - h, 2),
        "s2": round(pivot - (h - l), 2),
        "recent_high": round(max(highs), 2),
        "recent_low": round(min(lows), 2),
        "current_price": round(c, 2),
    }


def _event_risk_summary(lookback_hours: int = 24) -> Dict[str, Any]:
    """Placeholder event risk summary. Returns structured empty state."""
    # In full v1 this could call news/rss — for now return clean structure
    return {
        "macro_events": [],
        "fed_speakers": [],
        "major_economic": [],
        "geopolitical_alerts": [],
        "event_count": 0,
        "high_impact_today": False,
    }


def _build_base_output(job_name: str, job_type: str, mode: str) -> Dict[str, Any]:
    hkt = now_hkt()
    today = today_hkt()
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp_hkt": hkt.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "job_name": job_name,
        "job_type": job_type,
        "symbol": "XAUUSD",
        "market_status": _detect_market_status(hkt),
        "market_bias": "unknown",
        "volatility_regime": "unknown",
        "key_levels": {},
        "event_risk": {},
        "session_note": "",
        "trading_stance": "neutral",
        "confidence": 0,
        "source_window": {"start": "", "end": ""},
        "warnings": [],
        "generated_at": hkt.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }


def _generate_morning_briefing(bars: List[Dict], hkt_now: datetime, dry_run: bool) -> Dict[str, Any]:
    """08:15 HKT — overnight summary, key levels, event watch, initial bias."""
    out = _build_base_output(
        job_name="xauusd_morning_briefing",
        job_type="morning",
        mode="morning",
    )

    if not bars:
        out["warnings"].append("no bars available for morning briefing")
        out["session_note"] = "⚠️ No overnight data — market may be weekend/holiday."
        return out

    # Session note
    out["session_note"] = "🌅 XAUUSD Morning Briefing"

    # Source window: overnight (HKT 06:00 - 08:15)
    night_start = hkt_now.replace(hour=6, minute=0, second=0)
    out["source_window"] = {
        "start": night_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": hkt_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Market status
    status = _detect_market_status(hkt_now)
    out["market_status"] = status
    if status == "closed_weekend":
        out["warnings"].append("market closed (weekend)")
        out["session_note"] = "📅 Weekend — no live market. Briefing reflects last available data."
        return out

    # Bias
    out["market_bias"] = _detect_market_bias(bars)
    out["volatility_regime"] = _detect_volatility_regime(bars)

    # Key levels
    out["key_levels"] = _compute_key_levels(bars)
    kl = out["key_levels"]
    if kl:
        out["session_note"] = (
            f"🌅 XAUUSD Morning | {kl.get('current_price', '?')} | "
            f"Bias: {out['market_bias']} | Reg: {out['volatility_regime']}"
        )

    # Event risk
    out["event_risk"] = _event_risk_summary(lookback_hours=24)
    if out["event_risk"]["high_impact_today"]:
        out["warnings"].append("high-impact events today — elevated risk")

    # Trading stance
    bias = out["market_bias"]
    regime = out["volatility_regime"]
    if bias == "bullish" and regime in ("normal", "low"):
        out["trading_stance"] = "cautious_long"
        out["confidence"] = 0.65
    elif bias == "bearish" and regime in ("normal", "low"):
        out["trading_stance"] = "cautious_short"
        out["confidence"] = 0.65
    else:
        out["trading_stance"] = "neutral_watch"
        out["confidence"] = 0.45

    return out


def _generate_pre_london_briefing(bars: List[Dict], hkt_now: datetime, dry_run: bool) -> Dict[str, Any]:
    """14:45 HKT — asia summary, range expansion/compression, london risk, updated bias."""
    out = _build_base_output(
        job_name="xauusd_pre_london_refresh",
        job_type="pre_london",
        mode="pre_london",
    )

    if not bars:
        out["warnings"].append("no bars available")
        out["session_note"] = "⚠️ No data."
        return out

    status = _detect_market_status(hkt_now)
    out["market_status"] = status
    if status == "closed_weekend":
        out["warnings"].append("market closed (weekend)")
        return out

    # Source window: Asian session (HKT 07:00 - 14:45)
    asia_start = hkt_now.replace(hour=7, minute=0, second=0)
    out["source_window"] = {
        "start": asia_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": hkt_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Range analysis
    if len(bars) >= 10:
        import numpy as np
        recent_closes = [b["close"] for b in bars[-10:]]
        recent_highs = [b["high"] for b in bars[-10:]]
        recent_lows = [b["low"] for b in bars[-10:]]
        rng10 = max(recent_highs) - min(recent_lows)
        rng3 = max(recent_highs[-3:]) - min(recent_lows[-3:])
        if rng3 < rng10 * 0.5:
            range_regime = "compression"
            out["volatility_regime"] = "compressed"
        elif rng3 > rng10 * 0.9:
            range_regime = "expansion"
            out["volatility_regime"] = "high"
        else:
            range_regime = "normal"
            out["volatility_regime"] = "normal"
    else:
        range_regime = "unknown"

    out["market_bias"] = _detect_market_bias(bars)
    out["key_levels"] = _compute_key_levels(bars)
    kl = out["key_levels"]

    # London risk note
    london_open = hkt_now.replace(hour=15, minute=0, second=0)  # London 07:00 UTC = 15:00 HKT
    hours_to_london = (london_open - hkt_now).total_seconds() / 3600
    if hours_to_london > 0 and hours_to_london < 3:
        out["warnings"].append(f"London open in {hours_to_london:.1f}h — elevated volatility risk")

    event = _event_risk_summary()
    out["event_risk"] = event

    bias = out["market_bias"]
    stance_map = {
        ("bullish", "normal"): "long_bias",
        ("bullish", "compressed"): "breakout_long",
        ("bullish", "high"): "cautious_long",
        ("bearish", "normal"): "short_bias",
        ("bearish", "compressed"): "breakout_short",
        ("bearish", "high"): "cautious_short",
    }
    default_stance = "neutral_watch"
    out["trading_stance"] = stance_map.get((bias, out["volatility_regime"]), default_stance)
    out["confidence"] = 0.60 if bias != "unknown" else 0.40

    if kl:
        out["session_note"] = (
            f"🌏 Asia→London | {kl.get('current_price', '?')} | "
            f"Range: {range_regime} | {bias} | London: {hours_to_london:.1f}h away"
        )

    return out


def _generate_pre_ny_briefing(bars: List[Dict], hkt_now: datetime, dry_run: bool) -> Dict[str, Any]:
    """20:15 HKT — london summary, directional vs failed move, us event risk, ny expectation."""
    out = _build_base_output(
        job_name="xauusd_pre_ny_refresh",
        job_type="pre_ny",
        mode="pre_ny",
    )

    if not bars:
        out["warnings"].append("no bars available")
        out["session_note"] = "⚠️ No data."
        return out

    status = _detect_market_status(hkt_now)
    out["market_status"] = status
    if status == "closed_weekend":
        out["warnings"].append("market closed (weekend)")
        return out

    # Source window: London session (HKT 15:00 - 20:15)
    london_start = hkt_now.replace(hour=15, minute=0, second=0)
    out["source_window"] = {
        "start": london_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": hkt_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Directional move analysis
    if len(bars) >= 20:
        import numpy as np
        first_half = bars[-20:-10]
        second_half = bars[-10:]
        dir_score = np.mean([b["close"] for b in second_half]) - np.mean([b["close"] for b in first_half])
        dir_pct = dir_score / np.mean([b["close"] for b in first_half]) * 100
        if dir_pct > 0.3:
            move_type = "directional_long"
        elif dir_pct < -0.3:
            move_type = "directional_short"
        else:
            move_type = "range_bound"
    else:
        dir_pct = 0
        move_type = "unknown"

    out["market_bias"] = _detect_market_bias(bars)
    out["volatility_regime"] = _detect_volatility_regime(bars)
    out["key_levels"] = _compute_key_levels(bars)
    kl = out["key_levels"]

    event = _event_risk_summary()
    out["event_risk"] = event

    # NY open: HKT 21:30 = 13:30 UTC (summer)
    ny_start_hkt = hkt_now.replace(hour=21, minute=30, second=0)
    hours_to_ny = (ny_start_hkt - hkt_now).total_seconds() / 3600
    if hours_to_ny > 0 and hours_to_ny < 4:
        out["warnings"].append(f"NY open in {hours_to_ny:.1f}h — session start risk")

    # Stance
    bias = out["market_bias"]
    confidence = 0.70 if bias != "unknown" else 0.40
    if move_type == "range_bound":
        out["trading_stance"] = "range_watch_pre_ny"
    elif bias == "bullish" and move_type in ("directional_long",):
        out["trading_stance"] = "long_into_ny"
        confidence = 0.70
    elif bias == "bearish" and move_type in ("directional_short",):
        out["trading_stance"] = "short_into_ny"
        confidence = 0.70
    else:
        out["trading_stance"] = "neutral_pre_ny"

    out["confidence"] = confidence
    if kl:
        out["session_note"] = (
            f"🌃 Pre-NY | {kl.get('current_price', '?')} | "
            f"London: {move_type} | Bias: {bias} | NY in {hours_to_ny:.1f}h"
        )

    return out


def _format_telegram(output: Dict[str, Any]) -> str:
    """Build Telegram-friendly short summary from JSON output."""
    job_type = output.get("job_type", "?")
    price = output.get("key_levels", {}).get("current_price", "—")
    bias = output.get("market_bias", "??").upper()
    stance = output.get("trading_stance", "?")
    regime = output.get("volatility_regime", "?")
    conf = output.get("confidence", 0)
    kl = output.get("key_levels", {})
    warnings = output.get("warnings", [])

    bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪", "UNKNOWN": "⚪"}.get(bias, "⚪")

    lines = [
        f"*{job_type.upper()} REFRESH*",
        f"",
        f"XAUUSD {price}",
        f"{bias_emoji} {bias} | {regime}",
        f"Stance: {stance}",
        f"Confidence: {conf:.0%}",
    ]

    if kl.get("pivot"):
        lines.append(f"📍 Pivot {kl['pivot']} | R1 {kl.get('r1','?')} | S1 {kl.get('s1','?')}")

    if warnings:
        for w in warnings[:2]:
            lines.append(f"⚠️ {w}")

    lines.append(f"")
    lines.append(f"_{output['generated_at'][:10]}_")

    return "\n".join(lines)


# ── file output ───────────────────────────────────────────────────────────────

def _output_dir(mode: str) -> Path:
    base = Path("data/xauusd_refresh")
    return base / mode


def _write_json(output: Dict[str, Any], mode: str, hkt_now: datetime) -> Path:
    d = _output_dir(mode)
    d.mkdir(parents=True, exist_ok=True)
    ts = hkt_now.strftime("%Y%m%d_T%H%M")
    fpath = d / f"{ts}_refresh.json"
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    return fpath


def _write_md(output: Dict[str, Any], mode: str, hkt_now: datetime) -> Path:
    d = _output_dir(mode)
    d.mkdir(parents=True, exist_ok=True)
    ts = hkt_now.strftime("%Y%m%d_T%H%M")
    fpath = d / f"{ts}_refresh.md"
    lines = [
        f"# XAUUSD Refresh — {mode} | {output['generated_at'][:10]}",
        "",
        f"**Job:** {output['job_name']} | **Type:** {output['job_type']}",
        f"**Schema:** v{SCHEMA_VERSION}",
        f"**Market Status:** {output['market_status']}",
        f"**Bias:** {output['market_bias']} | **Regime:** {output['volatility_regime']}",
        f"**Stance:** {output['trading_stance']} | **Confidence:** {output['confidence']:.0%}",
        "",
        "## Key Levels",
        f"- Price: {output['key_levels'].get('current_price', '?')}",
        f"- Pivot: {output['key_levels'].get('pivot', '?')}",
        f"- R1: {output['key_levels'].get('r1', '?')} | R2: {output['key_levels'].get('r2', '?')}",
        f"- S1: {output['key_levels'].get('s1', '?')} | S2: {output['key_levels'].get('s2', '?')}",
        f"- High: {output['key_levels'].get('recent_high', '?')} | Low: {output['key_levels'].get('recent_low', '?')}",
        "",
        "## Session Note",
        f"{output['session_note']}",
        "",
        "## Warnings",
        *(f"- {w}" for w in output.get("warnings", []) or ["None"]),
        "",
        f"_Generated: {output['generated_at']}_",
    ]
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fpath


def _dispatch_telegram(output: Dict[str, Any], dry_run: bool) -> bool:
    """Send Telegram. Returns True on success, False on failure."""
    try:
        text = _format_telegram(output)
        if dry_run:
            print("[DRY RUN] Telegram message:")
            print(text)
            return True
        from hermes_tools import send_message
        import json as _json
        result = send_message(
            action="send",
            message=text,
            target=TELEGRAM_HOME,
        )
        # send_message returns a dict; check if it looks like success
        if isinstance(result, dict):
            ok = result.get("ok", False) or "message_id" in result or result.get("status") == "ok"
            return ok
        return False
    except Exception as e:
        logging.warning(f"Telegram dispatch failed: {e}")
        return False


# ── main logic ───────────────────────────────────────────────────────────────

def run(mode: str, dry_run: bool = False, force: bool = False) -> int:
    """Generate refresh briefing. Returns 0 on success, 1 on error, 2 on skip."""
    hkt_now = now_hkt()
    today = today_hkt()

    # Weekend skip
    if is_weekend(today) and not force:
        print(f"[{hkt_now}] Weekend skip — {today}")
        return 0

    # Mode to generator
    generators = {
        "morning": _generate_morning_briefing,
        "pre_london": _generate_pre_london_briefing,
        "pre_ny": _generate_pre_ny_briefing,
    }
    if mode not in generators:
        print(f"ERROR: unknown mode '{mode}'. Choose: {list(generators.keys())}")
        return 1

    generator = generators[mode]

    # Fetch data
    data = _fetch_gold_overnight(hkt_now=hkt_now)
    bars = data.get("bars", [])

    # Market closed detection
    if data.get("error"):
        status = _detect_market_status(hkt_now)
        if status == "closed_weekend" and not force:
            print(f"[{hkt_now}] Weekend/holiday skip")
            return 0
        print(f"WARNING: data fetch issue: {data.get('error')} — proceeding with available data")

    # Build output
    output = generator(bars, hkt_now, dry_run)

    # Add fetch warning if bars < 5
    if len(bars) < 5:
        output["warnings"].append(f"limited bar count ({len(bars)}) — interpret with caution")

    # Write files FIRST (always)
    json_path = _write_json(output, mode, hkt_now)
    md_path = _write_md(output, mode, hkt_now)
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")

    # Dispatch Telegram AFTER file write (best-effort)
    tg_ok = _dispatch_telegram(output, dry_run)
    if tg_ok:
        print("Telegram: OK")
    else:
        print("Telegram: FAILED (JSON/MD already saved)")

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="XAUUSD Briefing Refresh v1")
    p.add_argument("--mode", required=True,
                   choices=["morning", "pre_london", "pre_ny"],
                   help="Refresh mode")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview output without sending Telegram")
    p.add_argument("--force", action="store_true",
                   help="Force run even on weekends (for testing)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.mode, dry_run=args.dry_run, force=args.force))