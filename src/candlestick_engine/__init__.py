"""
Unified Gold Intelligence System — Candlestick Engine v3 M2

Phase: V3 M2 — Integration Layer
- EngineOutput contract for unified interface (V4 Fusion ready)
- CandleEngine.run() → CandleAnalysis → map_candle_to_engine_output() → EngineOutput

Rules (locked):
  • engulfing → real body based (NOT shadow-to-shadow)
  • pin_bar   → bullish / bearish two variants
  • doji      → body-to-range threshold
  • breakout  → close confirmation (close > ATR × 0.5)

structure_state enum (output): uptrend | downtrend | range | transition
(HH/HL computed internally; NOT exposed as enum values)

Version: 3.2.0
"""

from .engine import CandleEngine
from .contract import EngineOutput
from .mapper import map_candle_to_engine_output
from .models import (
    BiasDirection,
    BreakoutState,
    BreakoutType,
    CandleAnalysis,
    PatternDirection,
    PatternMatch,
    PatternName,
    StructureState,
    SupportResistance,
)

__all__ = [
    # Core engine
    "CandleEngine",
    # Output contract
    "EngineOutput",
    "map_candle_to_engine_output",
    # Models
    "CandleAnalysis",
    "PatternMatch",
    "PatternName",
    "PatternDirection",
    "BreakoutState",
    "BreakoutType",
    "StructureState",
    "SupportResistance",
    "BiasDirection",
]