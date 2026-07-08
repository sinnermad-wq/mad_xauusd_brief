"""states.py — state machine for direction/momentum/rejection/range/structure/sequence.

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from .features import CandleFeatures
from .structure import StructureState


# ── enums ─────────────────────────────────────────────────────────────────────

class DirectionState(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class MomentumState(Enum):
    ACCELERATING = "accelerating"
    DECELERATING = "decelerating"
    FLAT = "flat"
    UNKNOWN = "unknown"


class RejectionState(Enum):
    REJECTING_HIGH = "rejecting_high"
    REJECTING_LOW = "rejecting_low"
    HOLDING_HIGH = "holding_high"
    HOLDING_LOW = "holding_low"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class RangeState(Enum):
    COMPRESSED = "compressed"
    EXPANDING = "expanding"
    NORMAL = "normal"
    UNKNOWN = "unknown"


class SequenceState(Enum):
    BUILDING_LONG = "building_long"
    BUILDING_SHORT = "building_short"
    EXHAUSTING_BULL = "exhausting_bull"
    EXHAUSTING_BEAR = "exhausting_bear"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass
class CompositeStates:
    direction_state: str
    momentum_state: str
    rejection_state: str
    range_state: str
    structure_state: str
    sequence_state: str


def detect_direction_state(
    feat: CandleFeatures,
    prev_feats: List[CandleFeatures],
    structure_label: str,
) -> str:
    """Determine overall direction state."""
    if len(prev_feats) < 2:
        return DirectionState.UNKNOWN.value

    recent_closes = [f.close for f in prev_feats[-5:]]
    recent_closes.append(feat.close)
    up_moves = sum(1 for i in range(1, len(recent_closes)) if recent_closes[i] > recent_closes[i - 1])
    down_moves = sum(1 for i in range(1, len(recent_closes)) if recent_closes[i] < recent_closes[i - 1])

    # MA alignment
    ma_trend = "neutral"
    if feat.ema_distance_pct > 0.5:
        ma_trend = "bullish"
    elif feat.ema_distance_pct < -0.5:
        ma_trend = "bearish"

    bullish_signals = sum([
        feat.is_bullish,
        feat.close_position_in_range > 60,
        structure_label in ("higher_high", "higher_low", "neutral"),
        up_moves > down_moves,
        ma_trend == "bullish",
    ])
    bearish_signals = sum([
        feat.is_bearish,
        feat.close_position_in_range < 40,
        structure_label in ("lower_high", "lower_low", "neutral"),
        down_moves > up_moves,
        ma_trend == "bearish",
    ])

    if bullish_signals >= 4:
        return DirectionState.BULLISH.value
    elif bearish_signals >= 4:
        return DirectionState.BEARISH.value
    return DirectionState.NEUTRAL.value


def detect_momentum_state(
    feat: CandleFeatures,
    prev_feats: List[CandleFeatures],
) -> str:
    """Detect momentum state from candle characteristics."""
    if len(prev_feats) < 3:
        return MomentumState.UNKNOWN.value

    recent = prev_feats[-3:] + [feat]
    body_sizes = [f.body_size for f in recent]
    ranges = [f.full_range for f in recent]

    avg_body = sum(body_sizes) / len(body_sizes)
    avg_range = sum(ranges) / len(ranges)

    # Accelerating: current > avg
    accelerating = feat.body_size > avg_body * 1.2 and feat.full_range > avg_range * 1.15
    decelerating = feat.body_size < avg_body * 0.8 and feat.full_range < avg_range * 0.85

    if accelerating:
        return MomentumState.ACCELERATING.value
    elif decelerating:
        return MomentumState.DECELERATING.value
    return MomentumState.FLAT.value


def detect_rejection_state(
    feat: CandleFeatures,
    structure: StructureState,
) -> str:
    """Detect rejection / absorption at key levels."""
    # At recent high and wicking up then reversing
    if feat.at_recent_high and feat.upper_wick > feat.body_size * 1.5:
        return RejectionState.REJECTING_HIGH.value
    if feat.at_recent_low and feat.lower_wick > feat.body_size * 1.5:
        return RejectionState.REJECTING_LOW.value
    # Holding high: strong bullish close at/near high
    if feat.at_recent_high and feat.close_position_in_range >= 80:
        return RejectionState.HOLDING_HIGH.value
    if feat.at_recent_low and feat.close_position_in_range <= 20:
        return RejectionState.HOLDING_LOW.value
    if structure.is_sweep_high:
        return RejectionState.REJECTING_HIGH.value
    if structure.is_sweep_low:
        return RejectionState.REJECTING_LOW.value
    return RejectionState.NEUTRAL.value


def detect_range_state(feat: CandleFeatures) -> str:
    """Detect range/compression/expansion state."""
    comp = feat.compression_ratio_5
    exp = feat.expansion_ratio_5
    if comp < 0.65:
        return RangeState.COMPRESSED.value
    elif exp > 1.4:
        return RangeState.EXPANDING.value
    return RangeState.NORMAL.value


def detect_sequence_state(
    feat: CandleFeatures,
    structure: StructureState,
    direction_state: str,
    momentum_state: str,
) -> str:
    """Detect price sequence / exhaustion state."""
    label = structure.label

    if direction_state == DirectionState.BULLISH.value:
        if feat.at_recent_high and momentum_state == MomentumState.DECELERATING.value:
            return SequenceState.EXHAUSTING_BULL.value
        return SequenceState.BUILDING_LONG.value
    elif direction_state == DirectionState.BEARISH.value:
        if feat.at_recent_low and momentum_state == MomentumState.DECELERATING.value:
            return SequenceState.EXHAUSTING_BEAR.value
        return SequenceState.BUILDING_SHORT.value

    # Structure-based fallback
    if label in ("higher_high", "higher_low"):
        return SequenceState.BUILDING_LONG.value
    elif label in ("lower_high", "lower_low"):
        return SequenceState.BUILDING_SHORT.value
    return SequenceState.NEUTRAL.value


def build_composite_states(
    feat: CandleFeatures,
    prev_feats: List[CandleFeatures],
    structure: StructureState,
) -> CompositeStates:
    dir_state = detect_direction_state(feat, prev_feats, structure.label)
    mom_state = detect_momentum_state(feat, prev_feats)
    rej_state = detect_rejection_state(feat, structure)
    rng_state = detect_range_state(feat)
    seq_state = detect_sequence_state(feat, structure, dir_state, mom_state)

    return CompositeStates(
        direction_state=dir_state,
        momentum_state=mom_state,
        rejection_state=rej_state,
        range_state=rng_state,
        structure_state=structure.label,
        sequence_state=seq_state,
    )