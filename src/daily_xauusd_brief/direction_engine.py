"""Candlestick Direction Engine — manual-only analytical tool.

Purpose
-------
Multi-timeframe direction classification for XAUUSD ("主讀方向"/bias).
Read-only: does NOT auto-run, does NOT write to daily journals, does NOT
trigger any cron.  Operators invoke this module by hand for ad-hoc
analysis, backtest, or sanity check.

Status (as of 2026-07-06)
-------------------------
- Manual-only mode: NEVER bind to scheduled workflow.
- Long-term context: MA20 / MA50 / MA200 from D1 (≥ 5y yfinance fetch).
- Fallback: shorter period (6mo) D1 + flag ``insufficient_context``.
- Cached fetches: in-memory 5-minute TTL keyed by (symbol, period).
- Retry: tenacity-style exponential backoff on transient yfinance errors.

Public surface
--------------
- ``fetch_d1(symbol, period="5y") -> pd.DataFrame``
- ``compute_long_term_context(d1_df) -> dict``  — MA20/50/200 + flags
- ``build_engine_snapshot(symbol="GC=F") -> dict``  — full engine input
- ``EngineOutput``  dataclass holds bias / classification / confidence /
  levels / evidence / conflicts / session / candle_semantics.

Not part of public surface (intentionally restricted)
---------------------------------------------------
- Auto-publish to Telegram
- Auto-write to ``reports/``
- Auto-hook into ``run_daily.sh``
- Cron / scheduler integration

Usage example
------------
>>> from src.daily_xauusd_brief.direction_engine import build_engine_snapshot
>>> snap = build_engine_snapshot()           # manual use only
>>> snap["bias"], snap["confidence"]

"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SYMBOL = os.getenv("DIRECTION_ENGINE_SYMBOL", "GC=F")
DEFAULT_PERIOD = os.getenv("DIRECTION_ENGINE_PERIOD", "5y")    # yfinance period
FALLBACK_PERIOD = os.getenv("DIRECTION_ENGINE_FALLBACK_PERIOD", "6mo")
CACHE_TTL_SECONDS = 300   # 5-minute in-process cache

_HKT = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# A small retry helper — no external ``tenacity`` dependency for direction-
# engine tests; built inline against ``RuntimeError``/URLError exceptions.
# ---------------------------------------------------------------------------


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff sleep: 1s, 2s, 4s, 8s (capped)."""
    delay = min(2 ** (attempt - 1), 8)
    time.sleep(delay)


def fetch_with_retry(fn, *args, max_attempts: int = 3, **kwargs):
    """Call ``fn(*args, **kwargs)`` with exponential backoff on failure.

    Catches *all* exceptions raised by ``fn`` and retries up to
    ``max_attempts`` times.  The last exception is re-raised so callers can
    fall back gracefully.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "fetch_with_retry: attempt %d/%d failed (%s: %s)",
                attempt, max_attempts, type(exc).__name__, exc,
            )
            if attempt < max_attempts:
                _sleep_backoff(attempt)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    timestamp: float
    value: Any


_CACHE: dict[tuple, _CacheEntry] = {}


def _cache_get(key: tuple) -> Any | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    if (time.monotonic() - entry.timestamp) > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return entry.value


def _cache_set(key: tuple, value: Any) -> None:
    _CACHE[key] = _CacheEntry(timestamp=time.monotonic(), value=value)


# ---------------------------------------------------------------------------
# Data fetcher
# ---------------------------------------------------------------------------


def fetch_d1(symbol: str = DEFAULT_SYMBOL, period: str = DEFAULT_PERIOD) -> pd.DataFrame:
    """Fetch daily OHLC for ``symbol`` from yfinance.

    Args:
        symbol: yfinance ticker (e.g. ``"GC=F"``, ``"XAUUSD=X"``).
        period: yfinance ``period`` string.  Defaults to ``"5y"`` for
                full MA200 warm-up.

    Returns:
        ``pd.DataFrame`` with UTC DatetimeIndex (Open/High/Low/Close/Volume).
        The frame carries ``df.attrs["_fetch_period"]`` set to the
        *actually* used period (primary or fallback), for downstream
        transparency.

    Raises:
        RuntimeError: when both the requested period AND the fallback
                      ``FALLBACK_PERIOD`` (6mo) cannot be retrieved.

    The function:
        1. Uses in-memory TTL cache keyed by (symbol, period).
        2. Retries up to 3 times on transient errors.
        3. Falls back to a shorter period (``6mo``) on a final failure,
           so callers never crash — they receive a shorter frame.
    """
    import yfinance as yf

    cache_key = ("d1", symbol, period)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("fetch_d1 cache hit: %s", cache_key)
        cached.attrs["_fetch_period"] = cached.attrs.get("_fetch_period", period)
        return cached

    def _do_fetch(period_: str) -> pd.DataFrame:
        df = yf.Ticker(symbol).history(period=period_, interval="1d").dropna()
        if df.empty:
            raise RuntimeError(f"yfinance returned empty frame for {symbol} ({period_})")
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        df.attrs["_fetch_period"] = period_
        return df

    try:
        df = fetch_with_retry(_do_fetch, period, max_attempts=3)
        _cache_set(cache_key, df)
        logger.info("fetch_d1 OK: %s period=%s bars=%d", symbol, period, len(df))
        return df
    except Exception as primary_exc:  # noqa: BLE001
        logger.warning(
            "fetch_d1 primary period %s failed (%s) — falling back to %s",
            period, primary_exc, FALLBACK_PERIOD,
        )
        try:
            df = fetch_with_retry(_do_fetch, FALLBACK_PERIOD, max_attempts=2)
            _cache_set(("d1", symbol, FALLBACK_PERIOD), df)
            logger.info(
                "fetch_d1 fallback OK: %s period=%s bars=%d",
                symbol, FALLBACK_PERIOD, len(df),
            )
            return df
        except Exception as fallback_exc:  # noqa: BLE001
            raise RuntimeError(
                f"yfinance D1 fetch failed for {symbol}: "
                f"primary={primary_exc}, fallback={fallback_exc}"
            ) from fallback_exc


# ---------------------------------------------------------------------------
# Alias kept for backward compat / explicit semantic distinction.
# ---------------------------------------------------------------------------


# (No duplicate ``fetch_d1``; see canonical implementation above.)


# ---------------------------------------------------------------------------
# Ma200 / ma50 / ma20 long-term context
# ---------------------------------------------------------------------------


@dataclass
class LongTermContext:
    """Computed MA20/MA50/MA200 from D1."""

    close: float | None = None
    ma20: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    bars: int = 0
    period_used: str = ""
    sufficient_for_ma200: bool = False      # True iff bars >= 210
    sufficient_for_ma50: bool = False       # True iff bars >= 60
    insufficient_context: bool = False       # True when MA200 can't be computed

    def as_dict(self) -> dict:
        return asdict(self)


def compute_long_term_context(d1: pd.DataFrame, period_used: str) -> LongTermContext:
    """Compute MA20 / MA50 / MA200 from a D1 frame.

    Args:
        d1: D1 OHLC frame (UTC index).
        period_used: The period string used for the fetch — recorded for
            downstream transparency (e.g. ``"5y"`` vs ``"6mo"``).

    Returns:
        ``LongTermContext`` with .close / .ma20 / .ma50 / .ma200.
        Any NaN result is replaced by ``None``.
        Flags tell the caller which window is reliable.
    """
    ctx = LongTermContext(bars=len(d1), period_used=period_used)

    if d1.empty:
        ctx.insufficient_context = True
        return ctx

    ctx.sufficient_for_ma50 = len(d1) >= 60
    ctx.sufficient_for_ma200 = len(d1) >= 210

    close = d1["Close"].iloc[-1]
    ctx.close = float(close) if pd.notna(close) else None

    for window, attr in ((20, "ma20"), (50, "ma50"), (200, "ma200")):
        if len(d1) < window:
            continue
        ma = d1["Close"].rolling(window).mean().iloc[-1]
        ctx.__setattr__(attr, float(ma) if pd.notna(ma) else None)

    # Insufficient flag: True when MA200 is None (can't be computed)
    # OR bars < 210 even when MA200 happens to be numerically available
    # because it would be a stale rolling window.
    ctx.insufficient_context = (
        ctx.ma200 is None
        or len(d1) < 210
    )

    return ctx


# ---------------------------------------------------------------------------
# Public snapshot builder — minimal support; full analysis lives outside
# of the repo (manual analyst flow).
# ---------------------------------------------------------------------------


def build_engine_snapshot(
    symbol: str = DEFAULT_SYMBOL,
    period: str = DEFAULT_PERIOD,
) -> dict:
    """Build a snapshot dict capturing long-term context for XAUUSD.

    Intentionally compact.  This is the *input* feed for the manual
    analyst step (which lives in the operator's head or in ad-hoc chat
    prompts).  The Engine does NOT classify on its own here — that
    classification loop is human-in-the-loop.

    Raises:
        RuntimeError: when yfinance fetch fails for both primary
            ``period`` and fallback ``FALLBACK_PERIOD``.  Surface the
            error verbatim so the operator knows they have no D1 data.

    Returns:
        dict with keys::

            now_hkt            ISO timestamp (HKT)
            symbol             e.g. "GC=F"
            period_used        Which yfinance period succeeded
            primary_ok         True if ``period`` fetch OK; False if
                                fell back to ``FALLBACK_PERIOD``.
            provenance         "primary" | "fallback"
            bars               D1 bars returned
            d1_close           Last close
            d1_ma20            MA20  (or None)
            d1_ma50            MA50  (or None)
            d1_ma200           MA200 (or None)
            sufficient_for_ma50
            sufficient_for_ma200
            insufficient_context  True iff MA200 unavailable / bars < 210
    """
    d1 = fetch_d1(symbol, period)              # primary or fallback solved inside
    actual_period = d1.attrs.get("_fetch_period", period)
    primary_ok = actual_period == period

    ctx = compute_long_term_context(d1, actual_period)
    snapshot = {
        "now_hkt": datetime.now(_HKT).isoformat(timespec="seconds"),
        "symbol": symbol,
        "period_used": actual_period,
        "primary_ok": primary_ok,
        "provenance": "primary" if primary_ok else "fallback",
        "bars": ctx.bars,
        "d1_close": ctx.close,
        "d1_ma20": ctx.ma20,
        "d1_ma50": ctx.ma50,
        "d1_ma200": ctx.ma200,
        "sufficient_for_ma50": ctx.sufficient_for_ma50,
        "sufficient_for_ma200": ctx.sufficient_for_ma200,
        "insufficient_context": ctx.insufficient_context,
    }
    return snapshot


__all__ = [
    "DEFAULT_SYMBOL",
    "DEFAULT_PERIOD",
    "FALLBACK_PERIOD",
    "CACHE_TTL_SECONDS",
    "LongTermContext",
    "fetch_d1",
    "fetch_with_retry",
    "compute_long_term_context",
    "build_engine_snapshot",
]
