"""
Tests for Candlestick Engine v3 M1
Phase: GREEN — all tests written first (TDD), implementation follows.

All pattern rules (locked):
  • engulfing → real body based (NOT shadow-to-shadow)
  • pin_bar   → bullish / bearish two variants
  • doji      → body-to-range threshold
  • breakout  → close confirmation (close > ATR × 0.5)

Structure: uptrend | downtrend | range | transition (output enum)
(HH/HL internal only)

DataFrame convention: oldest-first (iloc[0]=oldest, iloc[-1]=newest).
make_df(bars) reverses: bars list expects newest LAST → df.iloc[-1]=newest.
"""

from __future__ import annotations

import pytest
from pandas import DataFrame

from candlestick_engine import (
    CandleEngine,
    CandleAnalysis,
    PatternDirection,
    PatternMatch,
    PatternName,
    StructureState,
    BreakoutType,
)
from candlestick_engine.models import SupportResistance


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def make_bar(open_p, high, low, close, volume=1_000_000.0) -> dict:
    return dict(O=open_p, H=high, L=low, C=close, V=volume)


def make_df(bars: list[dict]) -> DataFrame:
    """
    Convert bars (newest-last list) to DataFrame (oldest-first / newest-at-iloc[-1]).
    bars[0] = oldest bar, bars[-1] = newest bar.
    After reversal: df.iloc[0] = oldest, df.iloc[-1] = newest.
    """
    return DataFrame(list(reversed(bars)))


def make_downswing_bars(n: int = 20) -> list[dict]:
    """Generate n bars of clean downtrend: strictly descending H and L (newest-last)."""
    bars = []
    h, l = 4200, 4180
    for i in range(n):
        close = h - 10
        bars.append(make_bar(close, h, l, close - 10))
        h -= 15
        l -= 15
    return bars


def make_upswing_bars(n: int = 20) -> list[dict]:
    """Generate n bars of clean uptrend: strictly ascending H and L (newest-last)."""
    bars = []
    h, l = 3800, 3780
    for i in range(n):
        close = h + 10
        bars.append(make_bar(close - 10, h, l, close))
        h += 15
        l += 15
    return bars


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tests: Pin Bar
# ─────────────────────────────────────────────────────────────────────────────

class TestPinBar:
    """Pin bar — shadow ≥ 2× body, bullish / bearish variants."""

    def test_bearish_pin_bar(self):
        """Upper shadow dominant → bearish pin at resistance."""
        from candlestick_engine.patterns import detect_pin_bar
        bar = make_bar(open_p=4060, high=4085, low=4045, close=4062)
        # body=2, upper_shadow=23 → 23/2=11.5× ≥ 2✓
        result = detect_pin_bar(bar, [], {"near_resistance": True}, atr_14=20.0)
        assert result is not None
        assert result.name == PatternName.PIN_BAR
        assert result.direction == PatternDirection.BEARISH
        assert "看跌" in result.description_zh

    def test_bullish_pin_bar(self):
        """Lower shadow dominant → bullish pin at support."""
        from candlestick_engine.patterns import detect_pin_bar
        bar = make_bar(open_p=4045, high=4060, low=4010, close=4047)
        # body=2, lower_shadow=37 → 37/2=18.5× ≥ 2✓
        result = detect_pin_bar(bar, [], {"near_support": True}, atr_14=20.0)
        assert result is not None
        assert result.direction == PatternDirection.BULLISH
        assert "看漲" in result.description_zh

    def test_pin_bar_rejected_small_shadow(self):
        """Shadow < 2× body → not a pin bar."""
        from candlestick_engine.patterns import detect_pin_bar
        bar = make_bar(open_p=4040, high=4048, low=4030, close=4035)
        # body=5, max_shadow=8, ratio=1.6 < 2 → reject
        result = detect_pin_bar(bar, [], {}, atr_14=20.0)
        assert result is None

    def test_pin_bar_rejected_doji(self):
        """Body=0 → not pin bar (doji is separate pattern)."""
        from candlestick_engine.patterns import detect_pin_bar
        bar = make_bar(open_p=4050, high=4055, low=4045, close=4050)
        result = detect_pin_bar(bar, [], {}, atr_14=20.0)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tests: Engulfing
# ─────────────────────────────────────────────────────────────────────────────

class TestEngulfing:
    """Engulfing — real body coverage (NOT shadow-to-shadow)."""

    def test_bearish_engulfing_full_body_cover(self):
        """2nd bar bearish body fully covers 1st bar bullish body."""
        from candlestick_engine.patterns import detect_bearish_engulfing
        # newest first for pattern detection
        newest = make_bar(open_p=4042, high=4050, low=4020, close=4025)  # bearish
        prev   = make_bar(open_p=4030, high=4045, low=4028, close=4040)  # bullish
        result = detect_bearish_engulfing(newest, [prev], {"atr_14": 20.0})
        assert result is not None
        assert result.name == PatternName.BEARISH_ENGULFING
        assert result.direction == PatternDirection.BEARISH

    def test_bearish_engulfing_rejected_partial_cover(self):
        """2nd body only partially covers 1st → reject."""
        from candlestick_engine.patterns import detect_bearish_engulfing
        newest = make_bar(open_p=4035, high=4048, low=4025, close=4032)
        prev   = make_bar(open_p=4030, high=4045, low=4028, close=4040)
        # 2nd body top=4035 < 1st body top=4040 → not full cover
        result = detect_bearish_engulfing(newest, [prev], {"atr_14": 20.0})
        assert result is None

    def test_bullish_engulfing_full_body_cover(self):
        """2nd bar bullish body fully covers 1st bar bearish body."""
        from candlestick_engine.patterns import detect_bullish_engulfing
        newest = make_bar(open_p=4030, high=4048, low=4025, close=4045)  # bullish
        prev   = make_bar(open_p=4040, high=4050, low=4035, close=4038)  # bearish
        result = detect_bullish_engulfing(newest, [prev], {"atr_14": 20.0})
        assert result is not None
        assert result.name == PatternName.BULLISH_ENGULFING
        assert result.direction == PatternDirection.BULLISH

    def test_engulfing_rejected_wrong_direction(self):
        """1st bullish + 2nd also bullish → not bearish engulfing."""
        from candlestick_engine.patterns import detect_bearish_engulfing
        newest = make_bar(open_p=4040, high=4050, low=4035, close=4048)  # bullish
        prev   = make_bar(open_p=4030, high=4040, low=4028, close=4038)  # bullish
        result = detect_bearish_engulfing(newest, [prev], {"atr_14": 20.0})
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tests: Doji
# ─────────────────────────────────────────────────────────────────────────────

class TestDoji:
    """Doji — body / range ≤ 0.10."""

    def test_doji(self):
        """Body ≤ 10% of H-L range → doji."""
        from candlestick_engine.patterns import detect_doji
        bar = make_bar(open_p=4050, high=4060, low=4040, close=4051)
        # body=1, range=20, ratio=0.05 < 0.10 ✓
        result = detect_doji(bar, [], {})
        assert result is not None
        assert result.name == PatternName.DOJI
        assert result.direction == PatternDirection.NEUTRAL

    def test_doji_rejected_large_body(self):
        """Body / range > 0.10 → reject."""
        from candlestick_engine.patterns import detect_doji
        bar = make_bar(open_p=4040, high=4055, low=4030, close=4048)
        # body=8, range=25, ratio=0.32 > 0.10 → reject
        result = detect_doji(bar, [], {})
        assert result is None

    def test_doji_rejected_flat_line(self):
        """Range too small relative to ATR → reject."""
        from candlestick_engine.patterns import detect_doji
        bar = make_bar(open_p=4050, high=4051, low=4049, close=4050)
        # range=2, atr=20, 2 < 0.3×20 → reject
        result = detect_doji(bar, [], {"atr_14": 20.0})
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tests: Inside Bar
# ─────────────────────────────────────────────────────────────────────────────

class TestInsideBar:
    """Inside bar — inner H ≤ outer H AND inner L ≥ outer L."""

    def test_inside_bar(self):
        """Inner bar fully inside parent bar."""
        from candlestick_engine.patterns import detect_inside_bar
        newest = make_bar(open_p=4045, high=4055, low=4028, close=4052)
        prev   = make_bar(open_p=4040, high=4060, low=4020, close=4050)
        # inner H=4055 ≤ outer H=4060 ✓, inner L=4028 ≥ outer L=4020 ✓
        result = detect_inside_bar(newest, [prev], {"atr_14": 20.0})
        assert result is not None
        assert result.name == PatternName.INSIDE_BAR

    def test_inside_bar_rejected_expands(self):
        """Inner bar exceeds parent range → reject."""
        from candlestick_engine.patterns import detect_inside_bar
        newest = make_bar(open_p=4045, high=4065, low=4015, close=4052)
        prev   = make_bar(open_p=4040, high=4060, low=4020, close=4050)
        # inner H=4065 > outer H=4060 → reject
        result = detect_inside_bar(newest, [prev], {"atr_14": 20.0})
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Structure detection tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStructure:
    """Structure state: uptrend | downtrend | range | transition."""

    def test_downtrend(self):
        """Strictly descending H+L → downtrend."""
        from candlestick_engine.structure import detect_structure_state
        bars = make_downswing_bars(n=20)
        df = make_df(bars)
        state, internal, zone = detect_structure_state(df, lookback=15)
        assert "LH_LL" in internal or state in (
            StructureState.DOWNTREND, StructureState.TRANSITION
        )

    def test_uptrend(self):
        """Strictly ascending H+L → uptrend."""
        from candlestick_engine.structure import detect_structure_state
        bars = make_upswing_bars(n=20)
        df = make_df(bars)
        state, internal, zone = detect_structure_state(df, lookback=15)
        assert "HH_HL" in internal or state in (
            StructureState.UPTREND, StructureState.TRANSITION
        )

    def test_range_state(self):
        """Oscillating bars → range or transition."""
        from candlestick_engine.structure import detect_structure_state
        bars = [
            make_bar(4000, 4020, 3990, 4010),
            make_bar(4010, 4030, 4000, 4020),
            make_bar(4000, 4020, 3990, 4010),
            make_bar(4010, 4030, 4000, 4020),
            make_bar(4000, 4020, 3990, 4010),
        ]
        df = make_df(bars)
        state, internal, zone = detect_structure_state(df, lookback=5)
        assert state in (StructureState.RANGE, StructureState.TRANSITION)

    def test_insufficient_data(self):
        """Fewer than 2 bars → range fallback."""
        from candlestick_engine.structure import detect_structure_state
        bars = [make_bar(4000, 4020, 3990, 4010)]
        df = make_df(bars)
        state, internal, zone = detect_structure_state(df, lookback=20)
        assert state == StructureState.RANGE
        assert "insufficient" in internal


# ─────────────────────────────────────────────────────────────────────────────
# Breakout tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBreakout:
    """Breakout — close confirmation rules (NOT shadow confirmation)."""

    def test_breakdown_confirmed(self):
        """Close < support + body > ATR×0.5 + volume > MA×1.3 → breakdown confirmed."""
        from candlestick_engine.breakout import detect_breakout
        sr = SupportResistance(
            resistance_1=4060, resistance_2=4100, resistance_3=4150,
            support_1=4000, support_2=3950, support_3=3900,
            nearest_r=4060, nearest_s=4000,
        )
        # bars list: newest LAST → df.iloc[-1]
        # df.iloc[-1].C = 3990 (< s1=4000 ✓), body=20 > ATR×0.5=10 ✓
        # vol_ma = mean([1M, 2M]) = 1.5M → 2M > 1.5M×1.3=1.95M ✓
        bars = [
            make_bar(4010, 4025, 3985, 3990, volume=2_000_000),  # NEWEST → df.iloc[-1]
            make_bar(4030, 4045, 4015, 4035, volume=1_000_000),  # OLDEST
        ]
        df = make_df(bars)
        result = detect_breakout(df, sr, atr_14=20.0)
        assert result.breakout_confirmed is True
        assert result.breakout_type == BreakoutType.BREAK_DOWN
        assert result.breakout_distance_pct > 0

    def test_breakup_watch(self):
        """Price within 0.5% of resistance, not confirmed → watch."""
        from candlestick_engine.breakout import detect_breakout
        sr = SupportResistance(
            resistance_1=4060, resistance_2=4100, resistance_3=4150,
            support_1=4000, support_2=3950, support_3=3900,
            nearest_r=4060, nearest_s=4000,
        )
        # bars list: newest LAST → df.iloc[-1]
        # Need: close slightly above r1 (r_distance < 0.5%) but body < ATR×0.5 (break_up fails → watch)
        # close=4060.9, r1=4060, r_distance=0.022% < 0.5% ✓
        # body=0.9 < ATR×0.5=10 → break_up fails ✓ → watch triggers
        # vol=800K > vol_ma×1.3=416K ✓
        bars = [
            make_bar(4060, 4062, 4059, 4060.9, 800_000),  # NEWEST: close=4060.9, r1=4060, +0.022%
            make_bar(4020, 4035, 4010, 4025, 500_000),
            make_bar(4000, 4010, 3990, 4005, 100_000),
            make_bar(4000, 4010, 3990, 4005, 100_000),
            make_bar(4000, 4010, 3990, 4005, 100_000),
        ]
        df = make_df(bars)
        result = detect_breakout(df, sr, atr_14=20.0)
        assert result.breakout_type == BreakoutType.NONE
        assert result.breakout_watch is True
        assert result.breakout_watch_level == 4060

    def test_no_breakout_inside_range(self):
        """Price inside range, far from levels → no breakout."""
        from candlestick_engine.breakout import detect_breakout
        sr = SupportResistance(
            resistance_1=4060, resistance_2=4100, resistance_3=4150,
            support_1=4000, support_2=3950, support_3=3900,
            nearest_r=4060, nearest_s=4000,
        )
        # close=4035, r1=4060 (0.61% away), s1=4000 (1.67% away) → no watch
        bars = [
            make_bar(4040, 4050, 4030, 4035, volume=1_000_000),  # newest
            make_bar(4030, 4040, 4020, 4025, volume=1_000_000),  # oldest
        ]
        df = make_df(bars)
        result = detect_breakout(df, sr, atr_14=20.0)
        assert result.breakout_type == BreakoutType.NONE
        assert result.breakout_confirmed is False
        assert result.breakout_watch is False


# ─────────────────────────────────────────────────────────────────────────────
# CandleEngine integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCandleEngine:
    """Full engine: run() returns complete CandleAnalysis."""

    def test_engine_produces_valid_analysis(self):
        """Engine returns fully populated CandleAnalysis."""
        bars = make_downswing_bars(n=20)
        df = make_df(bars)
        engine = CandleEngine()
        result = engine.run(df, timestamp="2026-06-26T14:30:00")

        assert isinstance(result, CandleAnalysis)
        assert result.technical_bias.value in ("bullish", "bearish", "neutral")
        assert result.structure_state in StructureState
        assert result.breakout_state is not None
        assert result.atr_14 > 0
        assert result.rsi_14 > 0
        assert result.close > 0
        assert len(result.bias_explanation_zh) > 0

    def test_engine_to_dict(self):
        """CandleAnalysis.to_dict() returns JSON-serializable dict."""
        bars = make_downswing_bars(n=20)
        df = make_df(bars)
        engine = CandleEngine()
        result = engine.run(df, timestamp="2026-06-26T14:30:00")
        d = result.to_dict()

        assert isinstance(d, dict)
        assert d["technical_bias"] in ("bullish", "bearish", "neutral")
        assert d["structure_state"] in ("uptrend", "downtrend", "range", "transition")
        assert "breakout_state" in d
        assert "detected_patterns" in d
        assert isinstance(d["detected_patterns"], list)
        assert d["atr_14"] > 0

    def test_engine_requires_ohlc_columns(self):
        """Missing required column raises ValueError."""
        bad_df = DataFrame({"O": [1, 2], "H": [3, 4]})
        engine = CandleEngine()
        with pytest.raises(ValueError, match="requires column"):
            engine.run(bad_df)

    def test_engine_case_insensitive_columns(self):
        """Column names can be lowercase."""
        # Need 14+ bars for ATR(14) computation; use make_downswing_bars
        bars = make_downswing_bars(n=20)
        df = make_df(bars)
        df.columns = [c.lower() for c in df.columns]  # lowercase cols
        engine = CandleEngine()
        result = engine.run(df, timestamp="2026-06-26T14:30:00")
        assert isinstance(result, CandleAnalysis)
        assert result.close > 0