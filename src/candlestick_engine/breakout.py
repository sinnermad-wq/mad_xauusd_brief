"""
Breakout / Breakdown Detection v3 M1

Locked rule: breakout detection uses close confirmation (NOT intrabar spike).

DataFrame convention: oldest-first (iloc[0] = oldest, iloc[-1] = most recent).
Use iloc[-1] for current bar, iloc[-2] for previous bar.

Breakout Up confirmed when ALL of:
  1. Close > resistance level
  2. Body amplitude > ATR × 0.5 (close confirmation, NOT shadow)
  3. Volume > 5-bar vol MA × 1.3

Breakout Down confirmed when ALL of:
  1. Close < support level
  2. Body amplitude > ATR × 0.5
  3. Volume > 5-bar vol MA × 1.3
  4. Current close < previous close (confirms momentum)

Breakout Watch: price within 0.5% of level but not confirmed.
"""

from __future__ import annotations

import pandas as pd
from pandas import DataFrame

from .models import BreakoutState, BreakoutType, SupportResistance


def detect_breakout(
    df: DataFrame,
    sr: SupportResistance,
    atr_14: float,
    lookback_volume: int = 5,
) -> BreakoutState:
    """
    Detect confirmed breakouts / breakdowns using close confirmation.

    Parameters
    ----------
    df       : DataFrame with OHLCV (oldest-first, iloc[-1] = most recent bar)
    sr       : SupportResistance with nearest levels
    atr_14   : ATR-14 value for body threshold
    lookback_volume : bars for volume MA (default 5)

    Returns
    -------
    BreakoutState
    """
    if len(df) < 2 or atr_14 <= 0:
        return BreakoutState(
            breakout_type=BreakoutType.NONE,
            breakout_confirmed=False,
            breakout_distance_pct=0.0,
            breakout_watch=False,
            breakout_watch_level=None,
        )

    # ── Extract values ────────────────────────────────────────────────────────
    # DataFrame is oldest-first; most recent bar = iloc[-1]
    current_close = df["C"].iloc[-1]
    prev_close    = df["C"].iloc[-2]
    current_high  = df["H"].iloc[-1]
    current_low   = df["L"].iloc[-1]

    body          = abs(current_close - df["O"].iloc[-1])
    body_threshold = atr_14 * 0.5

    # ── Volume ───────────────────────────────────────────────────────────────
    vol_ma = df["V"].tail(lookback_volume).mean()
    vol_ok = df["V"].iloc[-1] > vol_ma * 1.3 if vol_ma > 0 else True

    # ── Resistance breakout (break_up) ──────────────────────────────────────
    r1 = sr.nearest_r
    break_up = (
        current_close > r1
        and body > body_threshold
        and vol_ok
    )

    # ── Support breakdown (break_down) ──────────────────────────────────────
    s1 = sr.nearest_s
    break_down = (
        current_close < s1
        and body > body_threshold
        and vol_ok
        and current_close < prev_close
    )

    # ── Build state ──────────────────────────────────────────────────────────
    if break_up:
        distance = (current_close - r1) / r1 * 100
        return BreakoutState(
            breakout_type=BreakoutType.BREAK_UP,
            breakout_confirmed=True,
            breakout_distance_pct=round(distance, 4),
            breakout_watch=False,
            breakout_watch_level=None,
        )
    if break_down:
        distance = (s1 - current_close) / s1 * 100
        return BreakoutState(
            breakout_type=BreakoutType.BREAK_DOWN,
            breakout_confirmed=True,
            breakout_distance_pct=round(distance, 4),
            breakout_watch=False,
            breakout_watch_level=None,
        )

    # ── Breakout watch (within 0.5% of level, not confirmed) ────────────────
    r_distance = (current_close - r1) / r1
    s_distance = (s1 - current_close) / s1
    r_watch    = 0 < r_distance < 0.005
    s_watch    = 0 < s_distance < 0.005

    if r_watch:
        return BreakoutState(
            breakout_type=BreakoutType.NONE,
            breakout_confirmed=False,
            breakout_distance_pct=round(r_distance * 100, 4),
            breakout_watch=True,
            breakout_watch_level=r1,
        )
    if s_watch:
        return BreakoutState(
            breakout_type=BreakoutType.NONE,
            breakout_confirmed=False,
            breakout_distance_pct=round(s_distance * 100, 4),
            breakout_watch=True,
            breakout_watch_level=s1,
        )

    return BreakoutState(
        breakout_type=BreakoutType.NONE,
        breakout_confirmed=False,
        breakout_distance_pct=0.0,
        breakout_watch=False,
        breakout_watch_level=None,
    )