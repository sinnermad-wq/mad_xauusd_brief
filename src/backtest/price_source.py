"""Price source loader — build PricingSeries from candlestick history.

MVP contract (per spec, Part 2):
- Reuse `data/history/candlestick/*` as raw price history
- Do NOT fetch external API
- Pure function: filesystem path → PricingSeries

Per history file:
- Extract `source_payload.timestamp` (signal emit time)
- Extract `source_payload.close`
- Each file = 1 bar (1D timeframe)
- Multiple runs on the same trading day → dedup-keeping-LATEST (file mtime newest)

Deterministic: same files → same series on every run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import PricingSeries
from .exceptions import PriceSourceError


def _extract_close(payload: dict) -> Optional[float]:
    """Extract close price from a candlestick source_payload, tolerate schema drift."""
    sp = payload.get("source_payload", payload)
    close = sp.get("close")
    if isinstance(close, (int, float)):
        return float(close)
    return None


def _extract_timestamp(payload: dict) -> Optional[str]:
    """Extract signal timestamp; tolerate both top-level and source_payload layouts."""
    ts = payload.get("timestamp") or payload.get("source_payload", {}).get("timestamp")
    if isinstance(ts, str):
        return ts
    return None


def load_pricing_series_from_candles(
    history_dir: Path | str,
    *,
    symbol_filter: str = "XAU",   # "XAU" matches "XAU/USD" and "XAUUSD"
) -> PricingSeries:
    """Walk `history_dir` (candlestick-only), build PricingSeries sorted ascending.

    Dedup rule: same trading day prefix (YYYY-MM-DD) → keep file with latest mtime.

    Args:
        history_dir: absolute path to `data/history/candlestick`
        symbol_filter: skip files whose symbol doesn't contain this token

    Returns:
        PricingSeries with (timestamps, closes) aligned ascending; len() may be 0
        if no usable file found.
    """
    p = Path(history_dir)
    if not p.exists():
        raise PriceSourceError(f"history_dir does not exist: {p}")

    # First pass: identify latest file per day
    latest_by_day: dict[str, tuple[float, str]] = {}   # day → (mtime, ts)
    for f in p.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise PriceSourceError(f"failed to parse {f.name}: {e}") from e

        sym = (data.get("source_payload", {}) or {}).get("symbol", "") or ""
        if symbol_filter and symbol_filter not in sym:
            continue

        ts = _extract_timestamp(data)
        close = _extract_close(data)
        if ts is None or close is None:
            continue

        day = ts[:10]
        mtime = f.stat().st_mtime
        prev = latest_by_day.get(day)
        if prev is None or mtime > prev[0]:
            latest_by_day[day] = (mtime, ts)

    if not latest_by_day:
        return PricingSeries(timestamps=(), closes=())

    # Sort by day key ascending; stable across re-runs (deterministic)
    sorted_days = sorted(latest_by_day.items())   # list of (day, (mtime, ts))

    # Second pass: get close for the kept file per day
    keep: dict[str, float] = {}
    keep_mtime: dict[str, float] = {}
    for f in p.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sym = (data.get("source_payload", {}) or {}).get("symbol", "") or ""
        if symbol_filter and symbol_filter not in sym:
            continue
        ts = _extract_timestamp(data)
        close = _extract_close(data)
        if ts is None or close is None:
            continue
        day = ts[:10]
        mtime = f.stat().st_mtime
        prev_mtime = keep_mtime.get(day)
        if prev_mtime is None or mtime > prev_mtime:
            keep[day] = close
            keep_mtime[day] = mtime

    timestamps = tuple(entry[1][1] for entry in sorted_days)
    closes = tuple(keep[day] for day, _ in sorted_days)
    return PricingSeries(timestamps=timestamps, closes=closes)