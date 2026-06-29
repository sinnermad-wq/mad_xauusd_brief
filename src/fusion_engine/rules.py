"""
V4 Fusion Engine — rules (pure functions).

Each rule is deterministic, JSON-safe, side-effect-free. Easy to unit-test
and to reason about in isolation.

We follow Confirm #1: default weights are
    candlestick 0.50 / briefing 0.20 / agreement 0.20 / quality 0.10
configurable via FusionConfig.weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import ConflictLabel, ConsensusLabel


# ── Default weights / thresholds (Confirm #1, #2, #4) ───────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "candlestick": 0.50,
    "briefing":    0.20,
    "agreement":   0.20,
    "quality":     0.10,
}

COUNTER_TREND_CONFIDENCE_CAP: float = 0.45   # Confirm #2
MIN_CONFIDENCE_FOR_TRADE_CANDIDATE: float = 0.65
MIN_BRIEFING_CONFIDENCE_FLOOR: float = 0.0    # briefings may not have
                                               # numeric confidence; treat 0
                                               # as "no penalty".


# ── Config object (passed in by engine) ──────────────────────────────────────


@dataclass
class FusionConfig:
    """Runtime-tunable weights & thresholds for the Fusion engine."""

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    counter_trend_cap: float = COUNTER_TREND_CONFIDENCE_CAP
    min_confidence_for_trade: float = MIN_CONFIDENCE_FOR_TRADE_CANDIDATE
    min_briefing_confidence_floor: float = MIN_BRIEFING_CONFIDENCE_FLOOR

    def total_weight(self) -> float:
        return sum(max(0.0, v) for v in self.weights.values()) or 1.0

    def normalized(self) -> dict[str, float]:
        total = self.total_weight()
        return {k: max(0.0, v) / total for k, v in self.weights.items()}


# ── Bias-keyed comparison helpers ────────────────────────────────────────────


def _bias_key(bias: Optional[str]) -> str:
    """Map any bias string to canonical key: bullish | bearish | neutral | unknown."""
    if bias in ("bullish", "long"):
        return "bullish"
    if bias in ("bearish", "short"):
        return "bearish"
    if bias == "neutral":
        return "neutral"
    return "unknown"


def _opposite(bias_key: str) -> str:
    return {
        "bullish": "bearish",
        "bearish": "bullish",
        "neutral": "neutral",
        "unknown": "unknown",
    }.get(bias_key, "unknown")


# ── Per-signal scoring rules (each returns 0.0–1.0 with optional reason) ────


def candlestick_score(
    *,
    candle_bias: Optional[str],
    candlestick_confidence: Optional[float],
    candle_validation_status: Optional[str],
    candle_trade_eligible: bool,
) -> float:
    """Score 0.0–1.0 reflecting candlestick signal quality for fusion.

    Rules (deterministic):
      • non_directional (neutral/unknown conf=None) → 0.30 (still some weight)
      • directional (bullish/bearish):
          base = candlestick_confidence (capped to 1.0, floor 0)
          * 1.0 if validation_status ∈ {ok, qualified_with_caution}
          * 0.5 if validation_status ∈ {degraded, unknown}
          * 0.0 if validation_status == invalid  (data hard-fail)
      • trade_eligible == False adds a flat -0.10 penalty (still contributes)
    """
    cb = _bias_key(candle_bias)
    if cb not in ("bullish", "bearish"):
        # neutral/unknown signals contribute modestly
        return 0.30

    conf = (
        candlestick_confidence
        if isinstance(candlestick_confidence, (int, float))
        else 0.5
    )
    base = max(0.0, min(1.0, conf))

    status = (candle_validation_status or "").lower()
    if status in ("ok", "qualified_with_caution"):
        quality_mult = 1.0
    elif status in ("degraded",):
        quality_mult = 0.5
    else:
        # unknown or invalid → be conservative
        quality_mult = 0.5 if status != "invalid" else 0.0

    score = base * quality_mult
    if not candle_trade_eligible:
        score = max(0.0, score - 0.10)
    return score


def briefing_score(
    *,
    briefing_bias: Optional[str],
    briefing_confidence: Optional[float] = None,
    briefing_present: bool = True,
    min_floor: float = MIN_BRIEFING_CONFIDENCE_FLOOR,
) -> float:
    """Score 0.0–1.0 reflecting briefing signal quality for fusion.

    Rules:
      • briefing absent → 0.0 (no signal; consumers must recompute agreement
        as 'insufficient_context'; not safely weighted into consensus).
      • non_directional bias → 0.20.
      • directional bias:
          base = max(min_floor, briefing_confidence?) else 0.5 fallback
          floor at min_floor (so absent numeric confidence != all-or-nothing).
    """
    if not briefing_present or briefing_bias is None:
        return 0.0

    bb = _bias_key(briefing_bias)
    if bb not in ("bullish", "bearish"):
        return 0.20

    if isinstance(briefing_confidence, (int, float)):
        raw = max(0.0, min(1.0, briefing_confidence))
    else:
        raw = 0.5  # soft default when briefing has no numeric confidence
    return max(min_floor, raw)


def agreement_score(
    *,
    candle_bias: Optional[str],
    briefing_bias: Optional[str],
    briefing_present: bool = True,
) -> float:
    """Score 0.0–1.0 reflecting how well candlestick & briefing agree.

    Returns a 0.0–1.0 value, NOT a consumes 0 entirely for disagreement —
    counter-trend still has some residual validity (Confirm: structured
    disagreement, not veto).

    • Insufficient context (no briefing)  → 0.50 (neutral, do not penalize)
    • Aligned (same direction)            → 1.00
    • Aligned with neutral briefing       → 0.70
    • One neutral, one directional        → 0.70
    • Counter-trend (opposite directions) → 0.10
    • Both unknown / both neutral         → 0.50
    """
    cb = _bias_key(candle_bias)
    bb = _bias_key(briefing_bias) if briefing_present else "unknown"

    if not briefing_present:
        return 0.50  # graceful, per Confirm #4
    if cb == "unknown" or bb == "unknown":
        return 0.40
    if cb == "neutral" and bb == "neutral":
        return 0.50
    if cb == "neutral" or bb == "neutral":
        # one neutral, one directional → soft leaning
        return 0.70
    if cb == bb:                          # bullish/bullish or bearish/bearish
        return 1.00
    # truly opposite directions
    return 0.10


def quality_score(
    *,
    candle_validation_status: Optional[str],
    candle_data_quality_flag: Optional[str],
    briefing_present: bool = True,
) -> float:
    """Score 0.0–1.0 reflecting data quality for fusion.

    Rules:
      • ok                                          → 1.00
      • qualified_with_caution                      → 0.80
      • degraded                                     → 0.50
      • invalid                                      → 0.00
      • missing briefing AND clean candlestick      → 0.85
    """
    v = (candle_validation_status or "").lower()
    d = (candle_data_quality_flag or "").lower()

    if v == "invalid":
        return 0.0
    if v == "degraded":
        return 0.5
    if v == "qualified_with_caution":
        return 0.8
    if v in ("ok", ""):
        # Validation ok or absent; fall back to data_quality_flag
        if d == "degraded":
            return 0.6
        return 1.0 if briefing_present else 0.85

    return 0.4  # unknown validation status


# ── Higher-level classifications ─────────────────────────────────────────────


def classify_consensus(
    *,
    candle_bias: Optional[str],
    briefing_bias: Optional[str],
    briefing_present: bool,
) -> str:
    """Map two biases (or the absence of one) to a ConsensusLabel."""
    cb = _bias_key(candle_bias)
    bb = _bias_key(briefing_bias) if briefing_present else "unknown"

    if not briefing_present:
        return ConsensusLabel.INSUFFICIENT_CONTEXT
    if cb == "unknown" or bb == "unknown":
        return ConsensusLabel.INSUFFICIENT_CONTEXT
    if cb == bb and cb in ("bullish", "bearish"):
        return ConsensusLabel.ALIGNED
    if (cb == "neutral") ^ (bb == "neutral"):
        return ConsensusLabel.PARTIALLY_ALIGNED
    if cb != bb and cb in ("bullish", "bearish") and bb in ("bullish", "bearish"):
        return ConsensusLabel.MIXED
    if cb == "neutral" and bb == "neutral":
        return ConsensusLabel.PARTIALLY_ALIGNED
    return ConsensusLabel.MIXED


def classify_conflict(
    *,
    candle_validation_status: Optional[str],
    candle_data_quality_flag: Optional[str],
    briefing_present: bool,
    consensus_label: str,
) -> str:
    """Pick a ConflictLabel reflecting *why* fusion is uncertain.

    Precedence (highest first):
      1. invalid / hard-fail data            → DATA_QUALITY_ISSUE
      2. missing briefing                    → MISSING_BRIEFING
      3. MIXED-style consensus                → COUNTER_TREND
      4. PARTIALLY_ALIGNED                   → MACRO_TECHNICAL_CONFLICT
      5. everything else                      → NONE
    """
    v = (candle_validation_status or "").lower()
    if v == "invalid" or (candle_data_quality_flag or "").lower() == "invalid":
        return ConflictLabel.DATA_QUALITY_ISSUE
    if not briefing_present:
        return ConflictLabel.MISSING_BRIEFING
    if consensus_label == ConsensusLabel.MIXED:
        return ConflictLabel.COUNTER_TREND
    if consensus_label == ConsensusLabel.PARTIALLY_ALIGNED:
        return ConflictLabel.MACRO_TECHNICAL_CONFLICT
    return ConflictLabel.NONE


# ── Final confidence (rule-based weighted + caps) ───────────────────────────


def compute_fusion_confidence(
    *,
    cs_score: float,
    br_score: float,
    ag_score: float,
    qu_score: float,
    conflict_label: str,
    cfg: Optional[FusionConfig] = None,
) -> float:
    """Compute final fusion_confidence (0.0–1.0).

    Apply Validate-1 weights (Confirm #1):
        fusion_conf = w_cs * cs_score + w_br * br_score + w_ag * ag_score
                    + w_qu * qu_score
    Then apply safety caps:
      • counter_trend  → cap at cfg.counter_trend_cap (default 0.45)
      • any other conflict label → minor cap at 0.70
      • no conflict → no additional cap

    Returned value is clipped to [0.0, 1.0].
    """
    cfg = cfg or FusionConfig()
    w = cfg.normalized()

    raw = (
        w.get("candlestick", 0) * cs_score
        + w.get("briefing", 0) * br_score
        + w.get("agreement", 0) * ag_score
        + w.get("quality", 0) * qu_score
    )

    if conflict_label == ConflictLabel.COUNTER_TREND:
        raw = min(raw, cfg.counter_trend_cap)
    elif conflict_label != ConflictLabel.NONE:
        raw = min(raw, 0.70)

    return max(0.0, min(1.0, raw))
