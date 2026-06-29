"""
Candlestick Engine - Main Orchestrator v3 M2
Phase: V3 M2 integration layer

Entry point: CandleEngine.run(ohlcv_df, timestamp) -> CandleAnalysis
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
import ta.momentum as mom
import ta.volatility as vol

from .breakout import detect_breakout
from .levels import find_support_resistance
from .models import (
    BiasDirection,
    BreakoutState,
    CandleAnalysis,
    PatternMatch,
    StructureState,
)
from .patterns import detect_all_patterns
from .structure import detect_structure_state


# ----------------------------------------------------------------------
# Indicator helpers
# ----------------------------------------------------------------------

def _compute_indicators(df: pd.DataFrame) -> dict:
    """Compute ATR-14, RSI-14, MA distances."""
    close = df["C"]
    high = df["H"]
    low = df["L"]

    atr_series = vol.average_true_range(high, low, close, window=14)
    atr = float(atr_series.iloc[-1]) if not atr_series.isna().all() else 20.0

    rsi_series = mom.rsi(close, window=14)
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.isna().all() else 50.0

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    cur = close.iloc[-1]
    m20 = float(ma20.iloc[-1]) if not ma20.isna().all() else cur
    m50 = float(ma50.iloc[-1]) if not ma50.isna().all() else cur
    m200 = float(ma200.iloc[-1]) if not ma200.isna().all() else cur

    return {
        "close": cur,
        "open_price": float(df["O"].iloc[-1]),
        "high": float(high.iloc[-1]),
        "low": float(low.iloc[-1]),
        "atr_14": atr,
        "rsi_14": rsi,
        "ma_distance_pct": {
            "ma20": round((cur - m20) / m20 * 100, 4) if m20 else 0.0,
            "ma50": round((cur - m50) / m50 * 100, 4) if m50 else 0.0,
            "ma200": round((cur - m200) / m200 * 100, 4) if m200 else 0.0,
        },
    }


# ----------------------------------------------------------------------
# Bias computation
# ----------------------------------------------------------------------

def _compute_bias(
    patterns: list[PatternMatch],
    structure: StructureState,
    breakout: BreakoutState,
    rsi_14: float,
    ma_dist: dict,
    atr_14: float,
) -> tuple[BiasDirection, float, str]:
    """Combine all signals into technical_bias + strength + Chinese explanation."""
    score = 0.0
    max_score = 0.0

    for p in patterns:
        d = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}.get(p.direction.value, 0.0)
        score += d * p.strength
        max_score += 1.0

    struct_map = {
        StructureState.UPTREND: 1.0,
        StructureState.DOWNTREND: -1.0,
        StructureState.RANGE: 0.0,
        StructureState.TRANSITION: 0.5,
    }
    score += struct_map.get(structure, 0.0)
    max_score += 1.0

    if breakout.breakout_confirmed:
        score += 2.0 if breakout.breakout_type.value == "break_up" else -2.0
        max_score += 2.0
    elif breakout.breakout_watch:
        score += 0.5 if breakout.breakout_watch_level else 0.0
        max_score += 0.5

    if rsi_14 > 70:
        score -= 0.5
        max_score += 0.5
    elif rsi_14 < 30:
        score += 0.5
        max_score += 0.5

    m20d = ma_dist.get("ma20", 0.0)
    if m20d > 2.0:
        score += 0.5
        max_score += 0.5
    elif m20d < -2.0:
        score -= 0.5
        max_score += 0.5

    strength = min(abs(score) / max_score, 1.0) if max_score > 0 else 0.5
    bias = BiasDirection.BULLISH if score > 0.3 else BiasDirection.BEARISH if score < -0.3 else BiasDirection.NEUTRAL

    explanations = []
    for p in patterns:
        if p.direction.value == "bullish":
            explanations.append("看漲: " + p.description_zh)
        elif p.direction.value == "bearish":
            explanations.append("看跌: " + p.description_zh)

    struct_words = {
        StructureState.UPTREND: "上升趨勢", StructureState.DOWNTREND: "下降趨勢",
        StructureState.RANGE: "區間震盪", StructureState.TRANSITION: "結構轉變",
    }
    explanations.append("結構: " + struct_words.get(structure, "未知"))

    if breakout.breakout_confirmed:
        btype = "向上突破" if breakout.breakout_type.value == "break_up" else "向下突破"
        explanations.append(btype)
    elif breakout.breakout_watch:
        explanations.append("突破觀察中")

    explanations.append(f"RSI={rsi_14:.1f}")
    bias_word = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(bias.value, "")
    explanation = f"[{bias_word}] " + " | ".join(explanations)

    return bias, round(strength, 4), explanation


# ----------------------------------------------------------------------
# Reversal watch
# ----------------------------------------------------------------------

def _reversal_watch(rsi_14: float, ma_dist: dict, breakout: BreakoutState) -> list[str]:
    watches = []
    if rsi_14 > 70:
        watches.append("RSI 超買(>70)，注意回調風險")
    elif rsi_14 < 30:
        watches.append("RSI 超賣(<30)，注意反彈機會")
    if ma_dist.get("ma20", 0) > 5:
        watches.append("價格偏離MA20逾5%，注意均值回歸")
    if breakout.breakout_watch and breakout.breakout_watch_level:
        watches.append(f"突破觀察：接近 {breakout.breakout_watch_level:.1f}")
    return watches


# ----------------------------------------------------------------------
# Pattern summary
# ----------------------------------------------------------------------

def _pattern_summary(patterns: list[PatternMatch]) -> str:
    if not patterns:
        return "無顯著型態"
    counts: dict[str, int] = {}
    for p in patterns:
        key = p.name.value
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{v} {k}" for k, v in counts.items())


# ----------------------------------------------------------------------
# CandleEngine
# ----------------------------------------------------------------------

class CandleEngine:
    """
    Run full candlestick analysis on OHLCV DataFrame.

    Required columns: O, H, L, C, V (case-insensitive).
    DataFrame: oldest-first (iloc[0] = oldest bar).
    """

    def run(self, df: pd.DataFrame, timestamp: str = "") -> CandleAnalysis:
        ts = timestamp or datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        symbol = "XAU/USD"
        lookback = min(len(df), 30)

        df = self._prepare_df(df)
        ind = _compute_indicators(df)
        atr_14 = ind["atr_14"]
        rsi_14 = ind["rsi_14"]
        ma_dist = ind["ma_distance_pct"]

        sr = find_support_resistance(df)

        bars = [df.iloc[i] for i in range(len(df))]
        bars_newest_first = bars[::-1]
        context = {
            "near_support": ind["close"] <= sr.support_1 * 1.01,
            "near_resistance": ind["close"] >= sr.resistance_1 * 0.99,
            "atr_14": atr_14,
        }
        detected = detect_all_patterns(bars_newest_first, context)
        structure, structure_internal, zone = detect_structure_state(df)
        breakout = detect_breakout(df, sr, atr_14)
        bias, bias_strength, explanation = _compute_bias(
            patterns=detected,
            structure=structure,
            breakout=breakout,
            rsi_14=rsi_14,
            ma_dist=ma_dist,
            atr_14=atr_14,
        )
        reversal_watch = _reversal_watch(rsi_14, ma_dist, breakout)
        pattern_summary_str = _pattern_summary(detected)

        support_levels = [sr.support_1, sr.support_2, sr.support_3]
        resistance_levels = [sr.resistance_1, sr.resistance_2, sr.resistance_3]

        return CandleAnalysis(
            timestamp=ts,
            symbol=symbol,
            analysis_window=f"{lookback} bars",
            bar_count=lookback,
            run_id=uuid.uuid4().hex[:12],
            technical_bias=bias,
            bias_strength=bias_strength,
            bias_explanation_zh=explanation,
            structure_state=structure,
            structure_internal=structure_internal,
            zone_assessment=zone,
            support_resistance=sr,
            breakout_state=breakout,
            detected_patterns=detected,
            pattern_summary=pattern_summary_str,
            reversal_watch=reversal_watch,
            atr_14=atr_14,
            rsi_14=rsi_14,
            ma_distance_pct=ma_dist,
            close=ind["close"],
            high=ind["high"],
            low=ind["low"],
            open_price=ind["open_price"],
            support_levels=support_levels,
            resistance_levels=resistance_levels,
        )

    def _prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        cols = {c: c.upper() for c in df.columns}
        required = {"O", "H", "L", "C"}
        missing = [r for r in required if r not in cols.values()]
        if missing:
            raise ValueError(f"CandlestickEngine requires column(s) {missing}")
        df = df.rename(columns=cols).copy()
        for c in ("O", "H", "L", "C"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        if "V" in df.columns:
            df["V"] = pd.to_numeric(df["V"], errors="coerce").fillna(1.0)
        else:
            df["V"] = 1.0
        return df.dropna(subset=["O", "H", "L", "C"]).reset_index(drop=True)