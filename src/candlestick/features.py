"""features.py — candlestick feature calculations.

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class CandleFeatures:
    """Computed features for a single candle plus rolling context."""
    # Raw OHLC
    open: float
    high: float
    low: float
    close: float
    volume: float

    # Body / wick
    body_size: float
    full_range: float
    upper_wick: float
    lower_wick: float
    body_pct_of_range: float     # 0-100
    close_position_in_range: float  # 0-100 (0=low, 100=high)

    # Direction
    is_bullish: bool
    is_bearish: bool
    is_doji: bool

    # Rolling context (recent window)
    rolling_range_5: float
    rolling_body_5: float
    rolling_range_10: float
    compression_ratio_5: float  # current range / avg(5-range)
    compression_ratio_10: float
    expansion_ratio_5: float    # current range / rolling avg
    expansion_ratio_10: float

    # Recent high/low
    recent_high: float
    recent_low: float
    at_recent_high: bool
    at_recent_low: bool
    above_recent_high: bool
    below_recent_low: bool

    # EMA distance (optional)
    ema_distance_pct: float      # (close - ema) / ema * 100

    # Rolling stats
    rolling_high_5: float
    rolling_low_5: float
    rolling_high_10: float
    rolling_low_10: float


def compute_features(
    df: pd.DataFrame,
    idx: int,
    ema_fast: Optional[float] = None,
    ema_slow: Optional[float] = None,
    recent_high: Optional[float] = None,
    recent_low: Optional[float] = None,
) -> CandleFeatures:
    """Compute all features for the bar at DataFrame index `idx`."""
    row = df.iloc[idx]
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    v = row.get("volume", 0)

    body = abs(c - o)
    rng = h - l
    upper = max(h - c, h - o) if c >= o else h - o
    lower = min(o - l, c - l) if c >= o else o - l

    body_pct = (body / rng * 100) if rng > 0 else 0.0
    close_pos = ((c - l) / rng * 100) if rng > 0 else 50.0

    # Rolling windows
    lookbacks = [5, 10]
    rolling_ranges: Dict[int, float] = {}
    rolling_bodies: Dict[int, float] = {}
    for n in lookbacks:
        start = max(0, idx - n + 1)
        window = df.iloc[start:idx + 1]
        ranges = window["high"] - window["low"]
        bodies = (window["close"] - window["open"]).abs()
        rolling_ranges[n] = float(np.mean(ranges))
        rolling_bodies[n] = float(np.mean(bodies))

    rng_5 = rolling_ranges[5]
    rng_10 = rolling_ranges[10]
    compression_5 = rng / rng_5 if rng_5 > 0 else 1.0
    compression_10 = rng / rng_10 if rng_10 > 0 else 1.0
    expansion_5 = rng_5 / rng if rng > 0 else 1.0
    expansion_10 = rng_10 / rng if rng > 0 else 1.0

    # Rolling high/low
    rh_5 = float(df.iloc[max(0, idx - 4):idx + 1]["high"].max())
    rl_5 = float(df.iloc[max(0, idx - 4):idx + 1]["low"].min())
    rh_10 = float(df.iloc[max(0, idx - 9):idx + 1]["high"].max())
    rl_10 = float(df.iloc[max(0, idx - 9):idx + 1]["low"].min())

    # EMA distance
    ema_dist = 0.0
    if ema_fast is not None and ema_fast > 0:
        ema_dist = (c - ema_fast) / ema_fast * 100.0

    return CandleFeatures(
        open=o, high=h, low=l, close=c, volume=v,
        body_size=body, full_range=rng,
        upper_wick=upper, lower_wick=lower,
        body_pct_of_range=body_pct,
        close_position_in_range=close_pos,
        is_bullish=c > o, is_bearish=c < o,
        is_doji=body < rng * 0.1 if rng > 0 else False,
        rolling_range_5=rng_5, rolling_body_5=rolling_bodies[5],
        rolling_range_10=rng_10,
        compression_ratio_5=compression_5,
        compression_ratio_10=compression_10,
        expansion_ratio_5=expansion_5,
        expansion_ratio_10=expansion_10,
        recent_high=rh_5, recent_low=rl_5,
        rolling_high_5=rh_5, rolling_low_5=rl_5,
        rolling_high_10=rh_10, rolling_low_10=rl_10,
        at_recent_high=h >= rh_5,
        at_recent_low=l <= rl_5,
        above_recent_high=c > rh_5,
        below_recent_low=c < rl_5,
        ema_distance_pct=ema_dist,
    )


def compute_ema(closes: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    return pd.Series(closes).ewm(span=period, adjust=False).mean().to_numpy()


def rolling_stats(df: pd.DataFrame, col: str, periods: List[int]) -> Dict[str, np.ndarray]:
    out = {}
    for n in periods:
        out[f"rolling_mean_{n}"] = df[col].rolling(n).mean().to_numpy()
        out[f"rolling_std_{n}"] = df[col].rolling(n).std().to_numpy()
    return out