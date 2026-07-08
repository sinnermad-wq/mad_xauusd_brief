"""patterns.py — basic candlestick pattern detection.

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List, Optional, Set

from .features import CandleFeatures, compute_features


def detect_doji(feat: CandleFeatures) -> bool:
    return feat.is_doji


def detect_inside_bar(
    feat: CandleFeatures,
    prev_feat: CandleFeatures,
) -> bool:
    """Inside bar: current range within prior bar range."""
    return (
        feat.high <= prev_feat.high and
        feat.low >= prev_feat.low
    )


def detect_bullish_engulfing(
    prev_feat: CandleFeatures,
    curr_feat: CandleFeatures,
) -> bool:
    """Bullish engulfing: small bearish prior, large bullish current engulfing it."""
    return (
        prev_feat.is_bearish and
        curr_feat.is_bullish and
        curr_feat.open < prev_feat.close and   # current opens below/near prior close
        curr_feat.close > prev_feat.open        # current closes above prior open
    )


def detect_bearish_engulfing(
    prev_feat: CandleFeatures,
    curr_feat: CandleFeatures,
) -> bool:
    """Bearish engulfing: small bullish prior, large bearish current engulfing it."""
    return (
        prev_feat.is_bullish and
        curr_feat.is_bearish and
        curr_feat.open > prev_feat.close and
        curr_feat.close < prev_feat.open
    )


def detect_hammer_like(
    feat: CandleFeatures,
    prev_feat: Optional[CandleFeatures] = None,
) -> bool:
    """Hammer-like: small body, long lower wick (≥2x body), close in upper 40% of range."""
    if feat.is_doji:
        return False
    cond1 = feat.lower_wick >= 2.0 * feat.body_size
    cond2 = feat.close_position_in_range >= 60.0
    cond3 = feat.upper_wick <= feat.body_size
    if prev_feat is not None:
        cond4 = feat.close > prev_feat.close   # bullish confirmation
        return cond1 and cond2 and cond3 and cond4
    return cond1 and cond2 and cond3


def detect_shooting_star_like(
    feat: CandleFeatures,
    prev_feat: Optional[CandleFeatures] = None,
) -> bool:
    """Shooting star-like: small body, long upper wick (≥2x body), close in lower 40%."""
    if feat.is_doji:
        return False
    cond1 = feat.upper_wick >= 2.0 * feat.body_size
    cond2 = feat.close_position_in_range <= 40.0
    cond3 = feat.lower_wick <= feat.body_size
    if prev_feat is not None:
        cond4 = feat.close < prev_feat.close   # bearish confirmation
        return cond1 and cond2 and cond3 and cond4
    return cond1 and cond2 and cond3


def detect_momentum_bar_up(
    feat: CandleFeatures,
    prev3_avg_range: float,
) -> bool:
    """Strong bullish momentum bar: bullish, large body, closes near high."""
    return (
        feat.is_bullish and
        not feat.is_doji and
        feat.body_pct_of_range >= 70.0 and
        feat.close_position_in_range >= 85.0 and
        feat.full_range > prev3_avg_range
    )


def detect_momentum_bar_down(
    feat: CandleFeatures,
    prev3_avg_range: float,
) -> bool:
    """Strong bearish momentum bar: bearish, large body, closes near low."""
    return (
        feat.is_bearish and
        not feat.is_doji and
        feat.body_pct_of_range >= 70.0 and
        feat.close_position_in_range <= 15.0 and
        feat.full_range > prev3_avg_range
    )


def scan_patterns(
    df: pd.DataFrame,
    lookback: int = 3,
) -> List[dict]:
    """Scan all candles and return pattern tags + metadata."""
    n = len(df)
    tags: List[Set[str]] = [set() for _ in range(n)]

    for i in range(1, n):
        feat = _get_feat(df, i)
        prev = _get_feat(df, i - 1) if i > 0 else None
        prev3_window = df.iloc[max(0, i - 3):i]
        prev3_range = float((prev3_window["high"] - prev3_window["low"]).mean()) if len(prev3_window) >= 2 else 0.0

        if detect_doji(feat):
            tags[i].add("doji")
        if prev and detect_inside_bar(feat, prev):
            tags[i].add("inside_bar")
        if prev and detect_bullish_engulfing(prev, feat):
            tags[i].add("bullish_engulfing")
        if prev and detect_bearish_engulfing(prev, feat):
            tags[i].add("bearish_engulfing")
        prev2 = _get_feat(df, i - 2) if i > 1 else None
        if detect_hammer_like(feat, prev2):
            tags[i].add("hammer_like")
        if detect_shooting_star_like(feat, prev2):
            tags[i].add("shooting_star_like")
        if detect_momentum_bar_up(feat, prev3_range):
            tags[i].add("momentum_bar_up")
        if detect_momentum_bar_down(feat, prev3_range):
            tags[i].add("momentum_bar_down")

    results = []
    for i in range(n):
        feat = _get_feat(df, i)
        results.append({
            "index": i,
            "timestamp": str(df.index[i]) if hasattr(df.index[i], "strftime") else str(df.index[i]),
            "close": feat.close,
            "tags": sorted(tags[i]),
        })
    return results


# ── internal helpers ────────────────────────────────────────────────────────────

_feat_cache: dict = {}


def _get_feat(df: pd.DataFrame, idx: int) -> CandleFeatures:
    key = id(df), idx
    if key not in _feat_cache:
        _feat_cache.clear()
        _feat_cache[key] = compute_features(df, idx)
    return _feat_cache[key]


def clear_cache():
    _feat_cache.clear()