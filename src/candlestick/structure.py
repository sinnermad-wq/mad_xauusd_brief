"""structure.py — market structure detection (HH/HL/LH/LL, breakouts, sweeps).

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class StructureLabel(Enum):
    HH = "higher_high"
    HL = "higher_low"
    LH = "lower_high"
    LL = "lower_low"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass
class StructureState:
    label: str
    is_breakout_up: bool
    is_breakout_down: bool
    is_failed_breakout_up: bool
    is_failed_breakout_down: bool
    is_sweep_high: bool
    is_sweep_low: bool
    is_reclaim_above: bool
    is_reclaim_below: bool
    recent_structure_points: List[Dict]  # [{type, price, idx}, ...]


def _find_swing_points(
    highs: np.ndarray, lows: np.ndarray,
    window: int = 5,
) -> Tuple[List[int], List[int]]:
    """Find swing highs/lows using simple n-bar lookback."""
    n = len(highs)
    swing_highs: List[int] = []
    swing_lows: List[int] = []

    for i in range(window, n - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            swing_highs.append(i)
        if lows[i] == min(lows[i - window:i + window + 1]):
            swing_lows.append(i)

    return swing_highs, swing_lows


def detect_structure(
    df: pd.DataFrame,
    lookback_swing: int = 20,
) -> StructureState:
    """
    Detect market structure: HH/HL/LH/LL, breakouts, failed breakouts, sweeps.
    """
    n = len(df)
    if n < 10:
        return StructureState(
            label="unknown",
            is_breakout_up=False, is_breakout_down=False,
            is_failed_breakout_up=False, is_failed_breakout_down=False,
            is_sweep_high=False, is_sweep_low=False,
            is_reclaim_above=False, is_reclaim_below=False,
            recent_structure_points=[],
        )

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()

    swing_highs, swing_lows = _find_swing_points(highs, lows, window=3)
    if not swing_highs or not swing_lows:
        return StructureState(
            label="neutral",
            is_breakout_up=False, is_breakout_down=False,
            is_failed_breakout_up=False, is_failed_breakout_down=False,
            is_sweep_high=False, is_sweep_low=False,
            is_reclaim_above=False, is_reclaim_below=False,
            recent_structure_points=[],
        )

    # Last N structure points
    last_h = swing_highs[-1]
    last_l = swing_lows[-1]

    # Check prior two of each
    def _get_price(points: List[int], key: str) -> Optional[float]:
        if not points:
            return None
        prices = [highs[p] if key == "high" else lows[p] for p in points]
        return prices[-1] if prices else None

    recent_hh = _get_price(swing_highs[-2:], "high")
    recent_hl = _get_price(swing_lows[-2:], "low")
    recent_lh = _get_price(swing_highs[-2:], "high")
    recent_ll = _get_price(swing_lows[-2:], "low")

    current_high = highs[-1]
    current_low = lows[-1]
    current_close = closes[-1]

    # HH / HL / LH / LL
    label = StructureLabel.NEUTRAL.value
    if recent_hh is not None and recent_hl is not None:
        if current_high > recent_hh and current_low > recent_hl:
            label = StructureLabel.HH.value
        elif current_high < recent_hh and current_low < recent_hl:
            label = StructureLabel.LL.value
        elif current_high > recent_hh and current_low <= recent_hl:
            label = StructureLabel.LH.value
        elif current_low < recent_hl and current_high <= recent_hh:
            label = StructureLabel.HL.value

    # Breakout detection
    prior_high = max(highs[-5:-1]) if n >= 5 else max(highs[:n - 1])
    prior_low = min(lows[-5:-1]) if n >= 5 else min(lows[:n - 1])

    is_breakout_up = current_high > prior_high and current_close > prior_high
    is_breakout_down = current_low < prior_low and current_close < prior_low

    # Failed breakout: breakout then reversal
    is_failed_breakout_up = (
        is_breakout_up and
        current_close < prior_high * 0.998
    )
    is_failed_breakout_down = (
        is_breakout_down and
        current_close > prior_low * 1.002
    )

    # Sweep high/low (wicks through key level)
    is_sweep_high = (
        highs[-1] > prior_high and
        lows[-1] <= prior_high
    )
    is_sweep_low = (
        lows[-1] < prior_low and
        highs[-1] >= prior_low
    )

    # Reclaim
    is_reclaim_above = (
        is_breakout_up and
        current_close > prior_high
    )
    is_reclaim_below = (
        is_breakout_down and
        current_close < prior_low
    )

    recent_points = [
        {"type": "swing_high", "price": float(highs[p]), "idx": int(p)}
        for p in swing_highs[-3:]
    ] + [
        {"type": "swing_low", "price": float(lows[p]), "idx": int(p)}
        for p in swing_lows[-3:]
    ]

    return StructureState(
        label=label,
        is_breakout_up=is_breakout_up,
        is_breakout_down=is_breakout_down,
        is_failed_breakout_up=is_failed_breakout_up,
        is_failed_breakout_down=is_failed_breakout_down,
        is_sweep_high=is_sweep_high,
        is_sweep_low=is_sweep_low,
        is_reclaim_above=is_reclaim_above,
        is_reclaim_below=is_reclaim_below,
        recent_structure_points=recent_points,
    )