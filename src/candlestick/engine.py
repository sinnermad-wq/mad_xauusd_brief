"""engine.py — main candlestick analysis engine.

Combines features + patterns + structure + states + scores → unified output.

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from .features import CandleFeatures, compute_features, compute_ema
from .patterns import detect_momentum_bar_up, detect_momentum_bar_down, scan_patterns
from .scores import Scores, compute_all_scores
from .states import CompositeStates, build_composite_states
from .structure import detect_structure


HKT = timezone(timedelta(hours=8))


@dataclass
class EngineConfig:
    """Configuration for the candlestick engine."""
    symbol: str = "GC=F"
    timeframe: str = "M1"
    lookback: int = 50       # bars to analyze
    ema_fast_period: int = 8
    ema_slow_period: int = 21
    # thresholds
    compression_threshold: float = 0.65
    expansion_threshold: float = 1.40
    strong_body_pct: float = 70.0
    strong_close_pos: float = 85.0


@dataclass
class EngineResult:
    """Full output from a single engine run."""
    # Schema fields
    schema_version: str = "1.0"
    timestamp: str = ""
    generated_at: str = ""
    symbol: str = ""
    timeframe: str = ""
    close: float = 0.0
    direction_bias: float = 0.0
    primary_state: str = "unknown"
    momentum_state: str = "unknown"
    rejection_state: str = "unknown"
    range_state: str = "unknown"
    structure_state: str = "unknown"
    sequence_state: str = "unknown"
    pattern_tags: List[str] = field(default_factory=list)
    momentum_score: float = 0.0
    rejection_score: float = 0.0
    compression_score: float = 0.0
    structure_score: float = 0.0
    confidence_score: float = 0.0
    context_tags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # Extra
    scores: Optional[Scores] = None
    latest_features: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "generated_at": self.generated_at,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "close": self.close,
            "direction_bias": self.direction_bias,
            "primary_state": self.primary_state,
            "momentum_state": self.momentum_state,
            "rejection_state": self.rejection_state,
            "range_state": self.range_state,
            "structure_state": self.structure_state,
            "sequence_state": self.sequence_state,
            "pattern_tags": self.pattern_tags,
            "momentum_score": self.momentum_score,
            "rejection_score": self.rejection_score,
            "compression_score": self.compression_score,
            "structure_score": self.structure_score,
            "confidence_score": self.confidence_score,
            "context_tags": self.context_tags,
            "warnings": self.warnings,
        }
        return d


class CandlestickEngine:
    """
    Rules-based, manual-only XAUUSD candlestick direction analysis engine.
    Designed for M1/M5 scalping research; outputs machine-readable states.
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        self.cfg = config or EngineConfig()

    def run(self, df: pd.DataFrame) -> EngineResult:
        """
        Analyze the last bar of df using the full feature/state/score pipeline.
        df must have columns: open, high, low, close, volume (optional).
        """
        warnings: List[str] = []
        n = len(df)

        if n < 10:
            warnings.append("insufficient bars for reliable analysis")
            return self._empty_result(warnings)

        # Limit lookback
        if n > self.cfg.lookback:
            df = df.tail(self.cfg.lookback).reset_index(drop=True)
            n = len(df)

        # Compute EMAs
        closes = df["close"].to_numpy()
        ema_fast = float(np.mean(closes[-self.cfg.ema_fast_period:]))
        ema_slow = float(np.mean(closes[-self.cfg.ema_slow_period:]))
        ema_fast_series = compute_ema(closes, self.cfg.ema_fast_period)
        ema_slow_series = compute_ema(closes, self.cfg.ema_slow_period)
        ema_fast_val = float(ema_fast_series[-1])

        # Compute features for all bars
        all_features: List[CandleFeatures] = []
        for i in range(n):
            ef = compute_features(df, i)
            all_features.append(ef)

        feat = all_features[-1]
        prev_feats = all_features[-6:-1]   # last 5 prior bars

        # Pattern scan
        pattern_results = scan_patterns(df, lookback=3)
        current_patterns = pattern_results[-1]["tags"]
        pattern_tags = sorted(current_patterns)

        # Detect structure
        structure = detect_structure(df, lookback_swing=min(20, n // 2))

        # Build composite states
        states = build_composite_states(feat, prev_feats, structure)

        # Compute scores
        scores = compute_all_scores(feat, states, set(pattern_tags))

        # Context tags
        context_tags = self._build_context_tags(
            feat, states, structure, ema_fast_val, ema_slow
        )

        # Extra warnings
        if len(df) < 20:
            warnings.append("short lookback — MA/direction may be unreliable")
        if feat.is_doji:
            warnings.append("doji candle — neutral signals possible")
        if feat.compression_ratio_5 < 0.65:
            warnings.append("compressed range — breakout likely")
        if structure.is_failed_breakout_up or structure.is_failed_breakout_down:
            warnings.append("failed breakout detected — reversal risk")

        # Build result
        now = datetime.now(HKT)
        return EngineResult(
            schema_version="1.0",
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            generated_at=now.isoformat(),
            symbol=self.cfg.symbol,
            timeframe=self.cfg.timeframe,
            close=round(feat.close, 2),
            direction_bias=scores.direction_bias,
            primary_state=scores.primary_state,
            momentum_state=states.momentum_state,
            rejection_state=states.rejection_state,
            range_state=states.range_state,
            structure_state=states.structure_state,
            sequence_state=states.sequence_state,
            pattern_tags=pattern_tags,
            momentum_score=scores.momentum_score,
            rejection_score=scores.rejection_score,
            compression_score=scores.compression_score,
            structure_score=scores.structure_score,
            confidence_score=scores.confidence_score,
            context_tags=context_tags,
            warnings=warnings,
            scores=scores,
            latest_features={
                "body_pct": round(feat.body_pct_of_range, 1),
                "close_pos": round(feat.close_position_in_range, 1),
                "ema_distance_pct": round(feat.ema_distance_pct, 3),
                "compression_ratio_5": round(feat.compression_ratio_5, 3),
                "expansion_ratio_5": round(feat.expansion_ratio_5, 3),
                "recent_high": round(feat.recent_high, 2),
                "recent_low": round(feat.recent_low, 2),
                "at_recent_high": feat.at_recent_high,
                "at_recent_low": feat.at_recent_low,
                "is_bullish": feat.is_bullish,
                "is_bearish": feat.is_bearish,
                "is_doji": feat.is_doji,
            },
        )

    def _build_context_tags(
        self,
        feat: CandleFeatures,
        states: CompositeStates,
        structure,
        ema_fast: float,
        ema_slow: float,
    ) -> List[str]:
        tags: List[str] = []

        # EMA alignment
        if ema_fast > ema_slow:
            tags.append("ema_bullish_alignment")
        else:
            tags.append("ema_bearish_alignment")

        # Trend strength
        if abs(feat.ema_distance_pct) > 1.0:
            tags.append("strong_ema_trend")
        elif abs(feat.ema_distance_pct) < 0.3:
            tags.append("ema_near_price")

        # Range
        if states.range_state == "compressed":
            tags.append("compressed_range")
        elif states.range_state == "expanding":
            tags.append("expanding_range")

        # Momentum
        if states.momentum_state == "accelerating":
            tags.append("momentum_accelerating")
        elif states.momentum_state == "decelerating":
            tags.append("momentum_decelerating")

        # Structure
        if structure.is_breakout_up:
            tags.append("breakout_up")
        if structure.is_breakout_down:
            tags.append("breakout_down")
        if structure.is_sweep_high:
            tags.append("swept_high")
        if structure.is_sweep_low:
            tags.append("swept_low")
        if structure.is_failed_breakout_up:
            tags.append("failed_breakout_up")
        if structure.is_failed_breakout_down:
            tags.append("failed_breakout_down")

        # Rejection
        if states.rejection_state in ("rejecting_high", "holding_high"):
            tags.append("rejection_at_high")
        if states.rejection_state in ("rejecting_low", "holding_low"):
            tags.append("rejection_at_low")

        # Session (approximate based on HKT hour)
        now = datetime.now(HKT)
        h = now.hour
        if 7 <= h < 15:
            tags.append("asian_session")
        elif 15 <= h < 22:
            tags.append("london_session")
        elif 21 <= h or h < 7:
            tags.append("ny_session")

        return tags

    def _empty_result(self, warnings: List[str]) -> EngineResult:
        return EngineResult(
            schema_version="1.0",
            timestamp=datetime.now(HKT).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            generated_at=datetime.now(HKT).isoformat(),
            symbol=self.cfg.symbol,
            timeframe=self.cfg.timeframe,
            warnings=warnings,
        )


# ── public helper ─────────────────────────────────────────────────────────────

def fetch_bars(
    symbol: str,
    period: str = "5d",
    interval: str = "1m",
) -> pd.DataFrame:
    """
    Fetch recent bars via yfinance.
    Falls back to 1d if minute data unavailable.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        # For M1/M5 we typically use shorter periods
        if interval in ("1m", "5m"):
            fetch_period = min(period, "7d")
        else:
            fetch_period = period
        df = ticker.history(start=None, period=fetch_period, interval=interval, auto_adjust=True)
        if df.empty:
            raise ValueError("yfinance returned empty DataFrame")
        df.columns = [c.lower() for c in df.columns]
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        return df
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {symbol} {interval}: {e}") from e