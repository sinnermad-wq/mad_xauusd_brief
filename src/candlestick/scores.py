"""scores.py — scoring system for bias/momentum/rejection/compression/structure/confidence.

Manual-only; no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from .features import CandleFeatures
from .states import CompositeStates


@dataclass
class Scores:
    direction_bias: float      # -1.0 to +1.0 (negative=bearish, positive=bullish)
    momentum_score: float       # 0-100
    rejection_score: float     # 0-100
    compression_score: float   # 0-100 (high = compressed)
    structure_score: float     # 0-100
    confidence_score: float    # 0-100
    primary_state: str
    secondary_states: List[str]


def compute_direction_bias(
    feat: CandleFeatures,
    states: CompositeStates,
    pattern_tags: Set[str],
) -> float:
    """Compute direction bias score -1.0 to +1.0."""
    signals: List[float] = []

    # Candle body
    if feat.is_bullish:
        signals.append(0.15 * (feat.body_pct_of_range / 100))
    elif feat.is_bearish:
        signals.append(-0.15 * (feat.body_pct_of_range / 100))

    # Close position
    cp = feat.close_position_in_range
    if cp >= 70:
        signals.append(0.20)
    elif cp >= 60:
        signals.append(0.10)
    elif cp <= 30:
        signals.append(-0.20)
    elif cp <= 40:
        signals.append(-0.10)

    # State
    if states.direction_state == "bullish":
        signals.append(0.25)
    elif states.direction_state == "bearish":
        signals.append(-0.25)

    # EMA distance
    ema = feat.ema_distance_pct
    if ema > 1.0:
        signals.append(0.15)
    elif ema > 0.5:
        signals.append(0.08)
    elif ema < -1.0:
        signals.append(-0.15)
    elif ema < -0.5:
        signals.append(-0.08)

    # Patterns
    if "bullish_engulfing" in pattern_tags:
        signals.append(0.30)
    if "bearish_engulfing" in pattern_tags:
        signals.append(-0.30)
    if "hammer_like" in pattern_tags:
        signals.append(0.20)
    if "shooting_star_like" in pattern_tags:
        signals.append(-0.20)
    if "momentum_bar_up" in pattern_tags:
        signals.append(0.25)
    if "momentum_bar_down" in pattern_tags:
        signals.append(-0.25)

    # Structure
    if states.structure_state in ("higher_high", "higher_low"):
        signals.append(0.10)
    elif states.structure_state in ("lower_high", "lower_low"):
        signals.append(-0.10)
    if states.structure_state == "higher_high":
        signals.append(0.05)

    score = sum(signals)
    return max(-1.0, min(1.0, score))


def compute_momentum_score(
    feat: CandleFeatures,
    states: CompositeStates,
    pattern_tags: Set[str],
) -> float:
    """Compute momentum score 0-100."""
    score = 50.0

    # From states
    if states.momentum_state == "accelerating":
        score += 20
    elif states.momentum_state == "decelerating":
        score -= 20

    # Body ratio
    body_pct = feat.body_pct_of_range
    if body_pct >= 80:
        score += 15
    elif body_pct >= 70:
        score += 8
    elif body_pct <= 30:
        score -= 15

    # Range expansion
    if feat.expansion_ratio_5 > 1.3:
        score += 10
    elif feat.expansion_ratio_5 < 0.7:
        score -= 10

    # Patterns
    if "momentum_bar_up" in pattern_tags:
        score += 15
    if "momentum_bar_down" in pattern_tags:
        score += 15
    if "doji" in pattern_tags:
        score -= 10

    return max(0.0, min(100.0, score))


def compute_rejection_score(
    feat: CandleFeatures,
    states: CompositeStates,
    pattern_tags: Set[str],
) -> float:
    """Compute rejection / absorption quality score 0-100."""
    score = 30.0

    if states.rejection_state == "rejecting_high":
        score += 25
        # High wick = rejection
        if feat.upper_wick > feat.body_size * 2:
            score += 15
    elif states.rejection_state == "rejecting_low":
        score += 25
        if feat.lower_wick > feat.body_size * 2:
            score += 15
    elif states.rejection_state == "holding_high":
        score += 20
    elif states.rejection_state == "holding_low":
        score += 20
    elif states.rejection_state == "neutral":
        score += 5

    # Compression before rejection = stronger
    if states.range_state == "compressed":
        score += 10

    # Failed breakout = failed rejection
    return max(0.0, min(100.0, score))


def compute_compression_score(
    feat: CandleFeatures,
    states: CompositeStates,
) -> float:
    """Compute compression score 0-100 (high = compressed)."""
    # compression_ratio < 1 means compressed
    cr = feat.compression_ratio_5
    if cr < 0.5:
        return 85.0
    elif cr < 0.65:
        return 70.0
    elif cr < 0.80:
        return 55.0
    elif cr > 1.5:
        return 20.0
    elif cr > 1.25:
        return 35.0
    return 50.0


def compute_structure_score(
    feat: CandleFeatures,
    states: CompositeStates,
) -> float:
    """Compute structure score 0-100."""
    score = 50.0

    label = states.structure_state
    if label == "higher_high":
        score = 75.0
    elif label == "higher_low":
        score = 65.0
    elif label == "lower_low":
        score = 25.0
    elif label == "lower_high":
        score = 35.0
    elif label == "neutral":
        score = 50.0

    # Breakouts add strength
    # Rejection quality adds
    if states.rejection_state in ("rejecting_high", "rejecting_low"):
        score = max(score, 65.0)

    return max(0.0, min(100.0, score))


def compute_confidence_score(
    scores: dict,
    states: CompositeStates,
    pattern_tags: Set[str],
) -> float:
    """Compute overall confidence score 0-100."""
    dir_abs = abs(scores["direction_bias"])
    mom = scores["momentum_score"] / 100.0
    structure = scores["structure_score"] / 100.0

    # High direction + momentum + structure = high confidence
    confidence = (dir_abs * 0.35 + mom * 0.30 + structure * 0.35) * 100

    # Pattern confirmation boosts
    if "bullish_engulfing" in pattern_tags or "hammer_like" in pattern_tags:
        confidence = min(100.0, confidence + 8)
    if "bearish_engulfing" in pattern_tags or "shooting_star_like" in pattern_tags:
        confidence = min(100.0, confidence + 8)

    # Compression = less certainty about direction
    if scores["compression_score"] > 70:
        confidence *= 0.85

    return max(0.0, min(100.0, confidence))


def compute_all_scores(
    feat: CandleFeatures,
    states: CompositeStates,
    pattern_tags: Set[str],
) -> Scores:
    dir_bias = compute_direction_bias(feat, states, pattern_tags)
    mom_score = compute_momentum_score(feat, states, pattern_tags)
    rej_score = compute_rejection_score(feat, states, pattern_tags)
    comp_score = compute_compression_score(feat, states)
    struct_score = compute_structure_score(feat, states)

    scores_dict = {
        "direction_bias": dir_bias,
        "momentum_score": mom_score,
        "rejection_score": rej_score,
        "compression_score": comp_score,
        "structure_score": struct_score,
    }
    conf_score = compute_confidence_score(scores_dict, states, pattern_tags)

    # Primary state = the most significant non-neutral state
    non_neutral = {
        "bullish": dir_bias,
        "bearish": -dir_bias,
        "accelerating": mom_score / 100 if states.momentum_state == "accelerating" else 0,
        "decelerating": mom_score / 100 if states.momentum_state == "decelerating" else 0,
        "compressed": comp_score / 100 if states.range_state == "compressed" else 0,
        "expanding": comp_score / 100 if states.range_state == "expanding" else 0,
    }
    primary = max(non_neutral, key=non_neutral.get) if any(v > 0.1 for v in non_neutral.values()) else "neutral"

    # Secondary states
    secondaries = []
    for s, v in non_neutral.items():
        if v > 0.2 and s != primary:
            secondaries.append(s)

    return Scores(
        direction_bias=round(dir_bias, 3),
        momentum_score=round(mom_score, 1),
        rejection_score=round(rej_score, 1),
        compression_score=round(comp_score, 1),
        structure_score=round(struct_score, 1),
        confidence_score=round(conf_score, 1),
        primary_state=primary,
        secondary_states=secondaries[:3],
    )