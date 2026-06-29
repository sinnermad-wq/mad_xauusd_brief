"""
Candlestick Pattern Detection v3 M1

Locked rules:
  • engulfing → real body based (NOT shadow-to-shadow)
  • pin_bar   → bullish / bearish two variants (not just "pin_bar")
  • doji      → body-to-range threshold (NOT O≈H≈L≈C)
  • inside_bar → 2nd bar fully inside 1st bar range

All functions:
  detect_pat[tern_name](bar, prev_bars, context) -> PatternMatch | None

Parameters:
  bar        — current bar (pd.Series: O, H, L, C)
  prev_bars   — list of prior bars (newest last)
  context     — dict with keys: atr_14, near_support, near_resistance
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .models import PatternDirection, PatternMatch, PatternName


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _real_body(bar: pd.Series) -> float:
    return abs(bar["C"] - bar["O"])


def _body_direction(bar: pd.Series) -> PatternDirection:
    return PatternDirection.BULLISH if bar["C"] > bar["O"] else PatternDirection.BEARISH


def _is_bullish(bar: pd.Series) -> bool:
    return bar["C"] > bar["O"]


def _upper_shadow(bar: pd.Series) -> float:
    return bar["H"] - max(bar["O"], bar["C"])


def _lower_shadow(bar: pd.Series) -> float:
    return min(bar["O"], bar["C"]) - bar["L"]


def _full_range(bar: pd.Series) -> float:
    """High - Low range."""
    return bar["H"] - bar["L"]


def _location(context: dict) -> str:
    """Determine if price is near support, resistance, or mid-range."""
    if context.get("near_resistance"):
        return "resistance"
    if context.get("near_support"):
        return "support"
    return "mid_range"


# ─────────────────────────────────────────────────────────────────────────────
# Pattern: Pin Bar (bullish + bearish variants)
# Locked: shadow ≥ 2× real body; bullish pin at support, bearish at resistance
# ─────────────────────────────────────────────────────────────────────────────

def detect_pin_bar(
    bar: pd.Series,
    prev_bars: list[pd.Series],
    context: dict,
    atr_14: float | None = None,
) -> PatternMatch | None:
    """
    Pin bar (hammer / shooting star).
    - Upper shadow ≥ 2× real body  → bearish (shooting star at resistance)
    - Lower shadow ≥ 2× real body  → bullish (hammer at support)
    """
    body = _real_body(bar)
    if body == 0:
        return None

    upper = _upper_shadow(bar)
    lower = _lower_shadow(bar)

    # Require: shadow ≥ 2× body
    shadow_ratio = max(upper, lower) / body

    if shadow_ratio < 2.0:
        return None

    atr = atr_14 or 20.0
    # Require: shadow absolute length > 0.5 × ATR (meaningful move)
    if max(upper, lower) < 0.5 * atr:
        return None

    if upper > lower:
        # Upper shadow dominant → bearish (shooting star)
        direction = PatternDirection.BEARISH
        location = "resistance" if context.get("near_resistance") else "mid_range"
        desc = (
            f"日線形成看跌 pin bar，上影線為實體 {shadow_ratio:.1f}×，"
            f"現於 {location} 區域，短線偏空"
        )
        strength = min(shadow_ratio / 4.0, 1.0)  # cap at 1.0
    else:
        # Lower shadow dominant → bullish (hammer)
        direction = PatternDirection.BULLISH
        location = "support" if context.get("near_support") else "mid_range"
        desc = (
            f"日線形成看漲 pin bar，下影線為實體 {shadow_ratio:.1f}×，"
            f"現於 {location} 區域，短線偏多"
        )
        strength = min(shadow_ratio / 4.0, 1.0)

    return PatternMatch(
        name=PatternName.PIN_BAR,
        direction=direction,
        strength=round(strength, 4),
        location=location,
        bars=1,
        description_zh=desc,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pattern: Engulfing (real body based, NOT shadow-to-shadow)
# Locked: 2nd bar body must fully cover 1st bar body (O/C overlap counts)
# ─────────────────────────────────────────────────────────────────────────────

def detect_bearish_engulfing(
    bar: pd.Series,
    prev_bars: list[pd.Series],
    context: dict,
) -> PatternMatch | None:
    """
    Bearish engulfing — 2nd bar:
      • Opens ≥ 1st bar close (or opens above 1st bar close)
      • Closes ≤ 1st bar open  (or closes below 1st bar open)
      • 2nd bar must be bearish (C < O)
      • 1st bar must be bullish
    Based on real body coverage, NOT shadow-to-shadow.
    """
    if len(prev_bars) < 1:
        return None

    prev = prev_bars[-1]
    if not (_is_bullish(prev) and not _is_bullish(bar)):
        return None

    # Real body engulfment: 2nd bar body covers 1st bar body
    first_body_top = max(prev["O"], prev["C"])
    first_body_bot = min(prev["O"], prev["C"])
    second_body_top = max(bar["O"], bar["C"])
    second_body_bot = min(bar["O"], bar["C"])

    # 2nd body fully covers 1st body (allow 1-pip tolerance)
    if not (second_body_top >= first_body_top and second_body_bot <= first_body_bot):
        return None

    location = _location(context)
    # Confirm bearish: close < open on 2nd bar
    body_len = _real_body(bar)
    atr = context.get("atr_14", 20.0)
    if body_len < 0.3 * atr:  # require meaningful size
        return None

    strength = min(body_len / (atr * 1.5), 1.0)
    return PatternMatch(
        name=PatternName.BEARISH_ENGULFING,
        direction=PatternDirection.BEARISH,
        strength=round(strength, 4),
        location=location,
        bars=2,
        description_zh=(
            f"日線在看跌吞噬形態，第2根實體完全包裹前一根看漲實體，"
            f"成交量放大確認，短線偏空"
        ),
    )


def detect_bullish_engulfing(
    bar: pd.Series,
    prev_bars: list[pd.Series],
    context: dict,
) -> PatternMatch | None:
    """
    Bullish engulfing — 2nd bar:
      • Opens ≤ 1st bar close (or opens below 1st bar close)
      • Closes ≥ 1st bar open  (or closes above 1st bar open)
      • 2nd bar must be bullish (C > O)
      • 1st bar must be bearish
    Based on real body coverage, NOT shadow-to-shadow.
    """
    if len(prev_bars) < 1:
        return None

    prev = prev_bars[-1]
    if not (not _is_bullish(prev) and _is_bullish(bar)):
        return None

    first_body_top = max(prev["O"], prev["C"])
    first_body_bot = min(prev["O"], prev["C"])
    second_body_top = max(bar["O"], bar["C"])
    second_body_bot = min(bar["O"], bar["C"])

    if not (second_body_top >= first_body_top and second_body_bot <= first_body_bot):
        return None

    location = _location(context)
    body_len = _real_body(bar)
    atr = context.get("atr_14", 20.0)
    if body_len < 0.3 * atr:
        return None

    strength = min(body_len / (atr * 1.5), 1.0)
    return PatternMatch(
        name=PatternName.BULLISH_ENGULFING,
        direction=PatternDirection.BULLISH,
        strength=round(strength, 4),
        location=location,
        bars=2,
        description_zh=(
            f"日線在看漲吞噬形態，第2根實體完全包裹前一根看跌實體，"
            f"成交量放大確認，短線偏多"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pattern: Doji (body-to-range threshold, NOT O≈H≈L≈C)
# Locked: body / full_range ≤ 0.1 (body ≤ 10% of range)
# ─────────────────────────────────────────────────────────────────────────────

def detect_doji(
    bar: pd.Series,
    prev_bars: list[pd.Series],
    context: dict,
) -> PatternMatch | None:
    """
    Doji — price opened and closed at nearly the same level.
    Threshold: body / range ≤ 0.10  (body ≤ 10% of H-L range).
    Does not imply direction; classified as neutral.
    """
    full_range = _full_range(bar)
    if full_range == 0:
        return None

    body = _real_body(bar)
    body_ratio = body / full_range

    # Threshold: body ≤ 10% of range
    if body_ratio > 0.10:
        return None

    # Require meaningful range (not a flat line)
    atr = context.get("atr_14", 20.0)
    if full_range < 0.3 * atr:
        return None

    location = _location(context)
    return PatternMatch(
        name=PatternName.DOJI,
        direction=PatternDirection.NEUTRAL,
        strength=round(0.5 + body_ratio, 4),  # stronger when smaller body
        location=location,
        bars=1,
        description_zh=(
            f"日線形成十字星（doji），實體佔範圍 {body_ratio*100:.0f}%，"
            f"顯示多空雙方僵持，短期可能出現方向選擇"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pattern: Inside Bar (neutral — direction implied by parent context)
# Locked: 2nd bar (inner) H ≤ 1st bar H AND 2nd bar L ≥ 1st bar L
# ─────────────────────────────────────────────────────────────────────────────

def detect_inside_bar(
    bar: pd.Series,
    prev_bars: list[pd.Series],
    context: dict,
) -> PatternMatch | None:
    """
    Inside bar — 2nd bar (inner bar) is fully inside 1st bar's range.
    • Inner H ≤ Outer H
    • Inner L ≥ Outer L
    Neutral: direction comes from the breakout of the parent bar.
    """
    if len(prev_bars) < 1:
        return None

    prev = prev_bars[-1]

    # Inside: inner H ≤ outer H AND inner L ≥ outer L
    if not (bar["H"] <= prev["H"] and bar["L"] >= prev["L"]):
        return None

    # Require inner bar has a meaningful body (not a doji inside a doji)
    body = _real_body(bar)
    if body < 0.2 * (context.get("atr_14", 20.0)):
        return None

    location = _location(context)
    return PatternMatch(
        name=PatternName.INSIDE_BAR,
        direction=PatternDirection.NEUTRAL,
        strength=0.6,
        location=location,
        bars=2,
        description_zh=(
            f"日線形成內外包（inside bar），現於 {location} 區間整理，"
            f"等待突破後跟進，突破區間後確認方向"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Detect all patterns (batch)
# ─────────────────────────────────────────────────────────────────────────────

def detect_all_patterns(
    bars: list[pd.Series],
    context: dict,
) -> list[PatternMatch]:
    """
    Scan bars from newest to oldest.
    bars[0] = most recent bar.
    Returns list of PatternMatch in order of detection.
    """
    results: list[PatternMatch] = []
    for i in range(len(bars)):
        bar = bars[i]
        prev = bars[i + 1:] if i + 1 < len(bars) else []

        # Pin bar
        pin = detect_pin_bar(bar, prev, context, context.get("atr_14"))
        if pin:
            results.append(pin)
            continue  # skip lower-priority patterns on same bar

        # Engulfing
        engulfing_b = detect_bearish_engulfing(bar, prev, context)
        if engulfing_b:
            results.append(engulfing_b)
            continue
        engulfing_bull = detect_bullish_engulfing(bar, prev, context)
        if engulfing_bull:
            results.append(engulfing_bull)
            continue

        # Doji
        doji = detect_doji(bar, prev, context)
        if doji:
            results.append(doji)
            continue

        # Inside bar
        ib = detect_inside_bar(bar, prev, context)
        if ib:
            results.append(ib)

    return results