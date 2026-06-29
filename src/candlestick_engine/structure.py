"""
Market Structure Detection v3 M1

Output enum: uptrend | downtrend | range | transition
(HH/HL logic is internal only; NOT exposed as enum values)

Rules:
  uptrend    = HH + HL (higher highs AND higher lows)
  downtrend  = LH + LL (lower highs AND lower lows)
  range      = no clear HH/HL or LH/LL pattern
  transition = break of recent high/low detected within lookback window

Locked rules:
  Structure detection uses CLOSE-BASED swing identification (not wicks).
  Breakout uses CLOSE CONFIRMATION (not intrabar spike).

DataFrame convention: oldest-first (iloc[0]=oldest, iloc[-1]=newest).
"""

from __future__ import annotations

import pandas as pd
from pandas import DataFrame

from .models import StructureState


def _identify_swing_points(
    highs: pd.Series | list,
    lows: pd.Series | list,
    window: int = 3,
) -> tuple[list[int], list[int]]:
    """
    Identify local swing highs and lows using a rolling window.

    A bar is a swing high if its high is STRICTLY GREATER than all bars
    in the [i-window, i+window] window. Same for swing low (strictly lower).

    Parameters
    ----------
    highs, lows : series/array of values
    window     : int (default 3) — half-window size

    Returns
    -------
    (swing_high_indices, swing_low_indices) — positions in the arrays
    """
    n = len(highs)
    swing_highs: list[int] = []
    swing_lows: list[int] = []

    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)

        h_val = highs[i]
        l_val = lows[i]

        window_h = highs[lo:hi]
        window_l = lows[lo:hi]

        # Strict maximum: no other bar in window has equal or higher high
        if h_val > window_h.max():
            swing_highs.append(i)

        # Strict minimum: no other bar in window has equal or lower low
        if l_val < window_l.min():
            swing_lows.append(i)

    return swing_highs, swing_lows


def _zone_assessment(closes: pd.Series, df: DataFrame) -> str:
    """
    Assess price position relative to MAs.
    `closes` is from `recent` (newest-first series, len = lookback).
    Uses head(n) for the most recent n bars of the series.
    """
    if len(closes) < 5:
        return "insufficient_data"

    # closes is from `recent` (newest-first after reversal)
    # head(n) = most recent n bars
    ma20 = closes.head(20).mean() if len(closes) >= 1 else None
    ma50 = closes.head(50).mean() if len(closes) >= 1 else None
    ma200_exists = len(closes) >= 200
    ma200 = closes.head(200).mean() if ma200_exists else None

    # Most recent bar (newest-first: iloc[0] = most recent)
    current = closes.iloc[0]

    above_ma20 = current > ma20 if ma20 is not None else False
    above_ma50 = current > ma50 if ma50 is not None else False
    above_ma200 = current > ma200 if ma200 is not None else False
    below_ma20 = current < ma20 if ma20 is not None else False
    below_ma50 = current < ma50 if ma50 is not None else False
    below_ma200 = current < ma200 if ma200 is not None else False

    if above_ma20 and above_ma50 and above_ma200:
        return "price_above_ma20_ma50_ma200"
    if below_ma20 and below_ma50 and below_ma200:
        return "price_below_ma20_ma50_ma200"
    if above_ma20 and above_ma50:
        return "price_above_ma20_ma50"
    if below_ma20 and below_ma50:
        return "price_below_ma20_ma50"
    if above_ma20:
        return "price_above_ma20"
    if below_ma20:
        return "price_below_ma20"
    return "price_near_ma20"


def _atr_from_df(df: DataFrame, period: int = 14) -> float:
    """Compute ATR-14 from OHLC DataFrame (oldest-first)."""
    if len(df) < 2:
        return 20.0
    trs = pd.concat([
        df["H"] - df["L"],
        (df["H"] - df["C"].shift(1)).abs(),
        (df["L"] - df["C"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return float(trs.tail(period).mean())


def detect_structure_state(
    df: DataFrame,
    lookback: int = 15,
    window: int = 3,
) -> tuple[StructureState, str, str]:
    """
    Determine market structure state from OHLC DataFrame.

    Parameters
    ----------
    df       : DataFrame with H, L, C columns (oldest-first, iloc[0]=oldest)
    lookback : number of most recent bars to analyse (default 15)
    window   : swing point detection half-window (default 3)

    Returns
    -------
    (StructureState, internal_code, zone_assessment)

    internal_code values:
      "HH_HL" = higher highs + higher lows (uptrend)
      "LH_LL" = lower highs + lower lows (downtrend)
      "RANGE" = no clear structure
      "BREAK_HIGH" / "BREAK_LOW" = transition state
      "flat_no_structure" = insufficient swing points
      "insufficient_data" = not enough bars
    """
    if len(df) < lookback:
        return StructureState.RANGE, "insufficient_data", "no_data"

    # DataFrame is oldest-first; tail() gives most recent N
    recent = df.tail(lookback).iloc[::-1]   # reverse → newest-first for iteration
    highs  = recent["H"].values
    lows   = recent["L"].values
    closes = recent["C"]

    # Identify swing points ONLY from recent bars (not full dataset)
    swing_high_indices, swing_low_indices = _identify_swing_points(highs, lows, window)
    swing_highs = [highs[i] for i in swing_high_indices]
    swing_lows  = [lows[i]  for i in swing_low_indices]

    # ── Check for transition (break of recent high/low) ─────────────────────
    # Look at most recent 3 bars for break detection
    last_3_high = highs[:3]
    last_3_low  = lows[:3]
    prev_high   = highs[3:].max() if len(highs) > 3 else highs.max()
    prev_low    = lows[3:].min()  if len(lows)  > 3 else lows.min()

    if len(last_3_high) > 0:
        recent_high = last_3_high.min()   # lowest of last 3 highs = most recent local high
        if recent_high > prev_high:
            return StructureState.TRANSITION, "BREAK_HIGH", _zone_assessment(closes, df)
    if len(last_3_low) > 0:
        recent_low = last_3_low.max()      # highest of last 3 lows = most recent local low
        if recent_low < prev_low:
            return StructureState.TRANSITION, "BREAK_LOW", _zone_assessment(closes, df)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Not enough structure — check if range is meaningful
        recent_range = highs.max() - lows.min()
        atr = _atr_from_df(df.tail(lookback), 14)
        if recent_range > atr * 3:
            return StructureState.RANGE, "flat_no_structure", _zone_assessment(closes, df)
        return StructureState.RANGE, "insufficient_structure", _zone_assessment(closes, df)

    # ── Compare most recent swing points (newest-first: indices [-1] = most recent) ──
    most_recent_sh  = swing_highs[-1]
    second_recent_sh = swing_highs[-2]
    most_recent_sl  = swing_lows[-1]
    second_recent_sl = swing_lows[-2]

    hh = most_recent_sh > second_recent_sh   # higher high
    hl = most_recent_sl > second_recent_sl   # higher low
    lh = most_recent_sh < second_recent_sh   # lower high
    ll = most_recent_sl < second_recent_sl   # lower low

    zone = _zone_assessment(closes, df)

    if hh and hl:
        return StructureState.UPTREND, "HH_HL", zone
    if lh and ll:
        return StructureState.DOWNTREND, "LH_LL", zone
    if hh or hl:
        return StructureState.TRANSITION, "HH_HL_partial", zone
    if lh or ll:
        return StructureState.TRANSITION, "LH_LL_partial", zone

    return StructureState.RANGE, "RANGE", zone