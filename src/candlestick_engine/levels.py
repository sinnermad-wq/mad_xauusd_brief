"""
Support & Resistance Level Detection v3 M1

Uses daily pivot points (classic floor pivot system).
Outputs 3 resistance levels and 3 support levels + nearest to current price.
"""

from __future__ import annotations

import pandas as pd

from .models import SupportResistance


def find_support_resistance(
    df: pd.DataFrame,
    lookback: int = 20,
) -> SupportResistance:
    """
    Find S/R levels using pivot points + rolling min/max touch count.

    Sources:
      - Floor pivots (PP, R1, R2, R3, S1, S2, S3)
      - Rolling touch count on recent swings

    Returns SupportResistance dataclass.
    """
    if len(df) < 3:
        return _default_sr(df)

    recent = df.tail(lookback).copy()
    current_close = df["C"].iloc[-1]
    current_high = df["H"].iloc[-1]
    current_low = df["L"].iloc[-1]

    # ── Classic floor pivot ──────────────────────────────────────────────────
    # DataFrame is oldest-first; iloc[0] = most recent bar
    last_high  = df["H"].iloc[0]
    last_low   = df["L"].iloc[0]
    prev_close = df["C"].iloc[1] if len(df) > 1 else df["C"].iloc[0]

    # Floor pivot formula
    pivot = (last_high + last_low + prev_close) / 3.0

    r1 = 2 * pivot - last_low
    s1 = 2 * pivot - last_high

    r2 = pivot + (last_high - last_low)
    s2 = pivot - (last_high - last_low)

    r3 = last_high + 2 * (pivot - last_low)
    s3 = last_low - 2 * (last_high - pivot)

    # ── Rolling touch count — find levels with multiple rejections ──────────
    # Use 0.5% proximity as "touch" threshold
    threshold_pct = 0.005

    def touches(level: float, series: pd.Series) -> int:
        return int((series - level).abs() / level < threshold_pct)

    highs = recent["H"]
    lows = recent["L"]
    closes = recent["C"]

    # Collect candidate levels with touch counts
    candidates: dict[str, float] = {}

    # Add pivot-derived levels
    for name, val in [("R3", r3), ("R2", r2), ("R1", r1),
                       ("P", pivot), ("S1", s1), ("S2", s2), ("S3", s3)]:
        if val > current_close:
            candidates[f"r_{name}"] = val
        else:
            candidates[f"s_{name}"] = val

    # Rolling swing highs/lows as additional levels
    swing_highs, swing_lows = _rolling_swing_levels(recent)
    for sh in swing_highs:
        if sh > current_close:
            candidates[f"rh_swing"] = max(candidates.get("rh_swing", 0), sh)
        else:
            candidates[f"rs_swing"] = min(candidates.get("rs_swing", float("inf")), sh)
    for sl in swing_lows:
        if sl < current_close:
            candidates[f"sl_swing"] = min(candidates.get("sl_swing", float("inf")), sl)
        else:
            candidates[f"sh_swing"] = max(candidates.get("sh_swing", 0), sl)

    # ── Build ordered S/R ───────────────────────────────────────────────────
    all_resistances = sorted(
        [v for k, v in candidates.items() if k.startswith("r")],
        reverse=True,
    )
    all_supports = sorted(
        [v for k, v in candidates.items() if k.startswith("s")],
    )

    def pick_levels(pool: list[float], count: int, current: float) -> list[float]:
        """Pick top N levels closest to current price from pool."""
        if not pool:
            return [current]
        sorted_pool = sorted(pool, key=lambda x: abs(x - current))
        return sorted_pool[:count]

    r_levels = pick_levels(all_resistances, 3, current_close)
    s_levels = pick_levels(all_supports, 3, current_close)

    r1_val = r_levels[0] if len(r_levels) > 0 else pivot + (r1 - pivot)
    r2_val = r_levels[1] if len(r_levels) > 1 else r1_val + (last_high - last_low) * 0.5
    r3_val = r_levels[2] if len(r_levels) > 2 else r2_val + (last_high - last_low) * 0.5

    s1_val = s_levels[0] if len(s_levels) > 0 else pivot - (pivot - s1)
    s2_val = s_levels[1] if len(s_levels) > 1 else s1_val - (last_high - last_low) * 0.5
    s3_val = s_levels[2] if len(s_levels) > 2 else s2_val - (last_high - last_low) * 0.5

    return SupportResistance(
        resistance_1=round(r1_val, 2),
        resistance_2=round(r2_val, 2),
        resistance_3=round(r3_val, 2),
        support_1=round(s1_val, 2),
        support_2=round(s2_val, 2),
        support_3=round(s3_val, 2),
        nearest_r=round(r1_val, 2),
        nearest_s=round(s1_val, 2),
    )


def _rolling_swing_levels(df: pd.DataFrame) -> tuple[list[float], list[float]]:
    """Find swing highs and lows using rolling window."""
    window = 5
    n = len(df)
    swing_h = []
    swing_l = []
    for i in range(window, n - window):
        if df["H"].iloc[i] == df["H"].iloc[i - window : i + window + 1].max():
            swing_h.append(df["H"].iloc[i])
        if df["L"].iloc[i] == df["L"].iloc[i - window : i + window + 1].min():
            swing_l.append(df["L"].iloc[i])
    return swing_h, swing_l


def _default_sr(df: pd.DataFrame) -> SupportResistance:
    """Fallback when data is insufficient."""
    close = df["C"].iloc[-1] if len(df) > 0 else 4000.0
    return SupportResistance(
        resistance_1=close * 1.01,
        resistance_2=close * 1.02,
        resistance_3=close * 1.03,
        support_1=close * 0.99,
        support_2=close * 0.98,
        support_3=close * 0.97,
        nearest_r=close * 1.01,
        nearest_s=close * 0.99,
    )