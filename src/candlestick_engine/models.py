"""
Candlestick Engine — Data Models
V3 M1

JSON output schema for CandleAnalysis.
All fields are required unless marked Optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class BiasDirection(str, Enum):
    """Technical bias direction."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class StructureState(str, Enum):
    """Market structure state (output enum)."""
    UPTREND = "uptrend"      # higher highs + higher lows
    DOWNTREND = "downtrend" # lower highs + lower lows
    RANGE = "range"         # no clear HH/HL or LH/LL
    TRANSITION = "transition"  # structure ambiguous or changing


class BreakoutType(str, Enum):
    BREAK_UP = "break_up"
    BREAK_DOWN = "break_down"
    NONE = "none"


class PatternDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class PatternName(str, Enum):
    PIN_BAR = "pin_bar"
    BEARISH_ENGULFING = "bearish_engulfing"
    BULLISH_ENGULFING = "bullish_engulfing"
    DOJI = "doji"
    INSIDE_BAR = "inside_bar"


@dataclass
class PatternMatch:
    """A detected candlestick pattern."""
    name: PatternName
    direction: PatternDirection  # bullish / bearish / neutral
    strength: float              # 0.0–1.0
    location: str                # e.g. "resistance", "support", "mid_range"
    bars: int                    # number of bars in pattern (typically 1–2)
    description_zh: str          # Chinese description of the pattern


@dataclass
class BreakoutState:
    """Breakout / breakdown detection."""
    breakout_type: BreakoutType  # break_up | break_down | none
    breakout_confirmed: bool    # True only if ALL rules satisfied
    breakout_distance_pct: float  # how far price moved from key level (%)
    breakout_watch: bool         # price near level, watching for confirmation
    breakout_watch_level: Optional[float] = None  # level being watched


@dataclass
class SupportResistance:
    """Key support and resistance levels."""
    resistance_1: float
    resistance_2: float
    resistance_3: float
    support_1: float
    support_2: float
    support_3: float
    nearest_r: float
    nearest_s: float


@dataclass
class CandleAnalysis:
    """Main output of CandleEngine — structured XAUUSD technical analysis."""
    # Identity
    timestamp: str           # ISO 8601, e.g. "2026-06-26T14:30:00"
    symbol: str = "XAU/USD"
    analysis_window: str = "1D"  # e.g. "30 bars" — display string for EngineOutput mapping
    bar_count: int = 0          # number of bars used for analysis (for bar-count display)
    run_id: str = ""            # unique run identifier (UUID, set by engine)

    # Core bias
    technical_bias: BiasDirection = BiasDirection.NEUTRAL
    bias_strength: float = 0.5          # 0.0–1.0
    bias_explanation_zh: str = ""        # Chinese explanation

    # Structure
    structure_state: StructureState = StructureState.RANGE
    structure_internal: str = ""         # internal HH/HL detail for debugging
    zone_assessment: str = ""           # e.g. "price_below_ma20_ma50"

    # Support / Resistance
    support_resistance: SupportResistance = field(default_factory=SupportResistance)
    support_levels:  list = field(default_factory=list)
    resistance_levels: list = field(default_factory=list)

    # Breakout
    breakout_state: BreakoutState = field(default_factory=BreakoutState)

    # Patterns
    detected_patterns: list[PatternMatch] = field(default_factory=list)
    pattern_summary: str = ""             # e.g. "1 bearish_engulfing, 1 doji"

    # Reversal watch
    reversal_watch: list[str] = field(default_factory=list)  # e.g. ["RSI divergence on 4H"]

    # Supplementary indicators
    atr_14: float = 0.0
    rsi_14: float = 50.0
    ma_distance_pct: dict = field(default_factory=dict)  # {"ma20": -2.8, ...}
    close: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open_price: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to dict for JSON dumping."""
        def _enum(v):
            return v.value if isinstance(v, Enum) else v

        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "analysis_window": self.analysis_window,
            "bar_count": self.bar_count,
            "run_id": self.run_id,
            "technical_bias": _enum(self.technical_bias),
            "bias_strength": round(self.bias_strength, 4),
            "bias_explanation_zh": self.bias_explanation_zh,
            "structure_state": _enum(self.structure_state),
            "structure_internal": self.structure_internal,
            "zone_assessment": self.zone_assessment,
            "support_resistance": {
                "resistance_1": self.support_resistance.resistance_1,
                "resistance_2": self.support_resistance.resistance_2,
                "resistance_3": self.support_resistance.resistance_3,
                "support_1": self.support_resistance.support_1,
                "support_2": self.support_resistance.support_2,
                "support_3": self.support_resistance.support_3,
                "nearest_r": self.support_resistance.nearest_r,
                "nearest_s": self.support_resistance.nearest_s,
            },
            "support_levels": self.support_levels,
            "resistance_levels": self.resistance_levels,
            "breakout_state": {
                "breakout_type": _enum(self.breakout_state.breakout_type),
                "breakout_confirmed": self.breakout_state.breakout_confirmed,
                "breakout_distance_pct": round(self.breakout_state.breakout_distance_pct, 4),
                "breakout_watch": self.breakout_state.breakout_watch,
                "breakout_watch_level": self.breakout_state.breakout_watch_level,
            },
            "detected_patterns": [
                {
                    "name": _enum(p.name),
                    "direction": _enum(p.direction),
                    "strength": round(p.strength, 4),
                    "location": p.location,
                    "bars": p.bars,
                    "description_zh": p.description_zh,
                }
                for p in self.detected_patterns
            ],
            "pattern_summary": self.pattern_summary,
            "reversal_watch": self.reversal_watch,
            "atr_14": round(self.atr_14, 4),
            "rsi_14": round(self.rsi_14, 2),
            "ma_distance_pct": {k: round(v, 4) for k, v in self.ma_distance_pct.items()},
            "close": round(self.close, 5),
            "high": round(self.high, 5),
            "low": round(self.low, 5),
            "open": round(self.open_price, 5),
        }