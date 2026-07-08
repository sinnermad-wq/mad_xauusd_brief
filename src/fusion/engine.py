"""engine.py — fusion scoring + decision logic.

Combines briefing/refresh (context layer) + candlestick engine (price action)
into a single decision. Read-only; no broker / execution / auto-trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set

HKT = timezone(timedelta(hours=8))


# ── Enums ────────────────────────────────────────────────────────────────────

class Decision(str, Enum):
    LONG_WATCH    = "long_watch"
    SHORT_WATCH   = "short_watch"
    WAIT          = "wait"
    NO_TRADE      = "no_trade"


class BiasDirection(str, Enum):
    BULLISH  = "bullish"
    BEARISH  = "bearish"
    NEUTRAL  = "neutral"
    MIXED    = "mixed"


class RiskState(str, Enum):
    LOW      = "low"
    MODERATE = "moderate"
    HIGH     = "high"
    EXTREME  = "extreme"
    UNKNOWN  = "unknown"


class MarketRegime(str, Enum):
    TRENDING_UP     = "trending_up"
    TRENDING_DOWN   = "trending_down"
    RANGING         = "ranging"
    VOLATILE        = "volatile"
    COMPRESSED      = "compressed"
    UNKNOWN         = "unknown"


class EntryReadiness(str, Enum):
    READY         = "ready"
    CONDITIONAL   = "conditional"
    NOT_READY     = "not_ready"


# ── Input containers ──────────────────────────────────────────────────────────

@dataclass
class BriefingInput:
    """Output from generate_xauusd_refresh.py."""
    market_bias:     str          = "unknown"
    volatility_regime: str        = "unknown"
    event_risk:       Dict[str, Any] = field(default_factory=dict)
    trading_stance:  str          = "unknown"
    effective_trading_stance: str = "unknown"
    confidence:       float        = 0.0
    key_levels:       Dict[str, Any] = field(default_factory=dict)
    warnings:         List[str]   = field(default_factory=list)
    timestamp_hkt:    str          = ""
    job_name:         str          = ""
    job_type:         str          = ""
    symbol:           str          = "GC=F"
    market_status:    str          = "unknown"
    session_note:     str          = ""
    schema_version:   str          = "1.0"
    # Raw fields if loaded from JSON directly
    _raw: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BriefingInput":
        return cls(
            market_bias=d.get("market_bias", "unknown"),
            volatility_regime=d.get("volatility_regime", "unknown"),
            event_risk=d.get("event_risk", {}),
            trading_stance=d.get("trading_stance", "unknown"),
            effective_trading_stance=d.get("effective_trading_stance", d.get("trading_stance", "unknown")),
            confidence=float(d.get("confidence", 0)),
            key_levels=d.get("key_levels", {}),
            warnings=d.get("warnings", []),
            timestamp_hkt=d.get("timestamp_hkt", d.get("timestamp", "")),
            job_name=d.get("job_name", ""),
            job_type=d.get("job_type", ""),
            symbol=d.get("symbol", "GC=F"),
            market_status=d.get("market_status", "unknown"),
            session_note=d.get("session_note", ""),
            schema_version=d.get("schema_version", "1.0"),
            _raw=d,
        )

    @property
    def is_stale(self) -> bool:
        """True if briefing is older than 8 hours."""
        if not self.timestamp_hkt:
            return True
        try:
            ts = datetime.fromisoformat(self.timestamp_hkt.replace("Z", "+00:00"))
            age = datetime.now(HKT) - ts
            return age.total_seconds() > 8 * 3600
        except Exception:
            return True

    @property
    def is_market_closed(self) -> bool:
        return self.market_status in ("closed", "closed_weekend", "market_closed")

    @property
    def has_high_event_risk(self) -> bool:
        er = self.event_risk or {}
        if er.get("high_impact_today"):
            return True
        if er.get("geopolitical_alerts"):
            alerts = er["geopolitical_alerts"]
            if isinstance(alerts, list) and len(alerts) > 0:
                return True
            if isinstance(alerts, str) and alerts.strip():
                return True
        return False


@dataclass
class CandleInput:
    """Output from run_candle_engine.py."""
    direction_bias:    float        = 0.0   # -1 to +1
    primary_state:     str          = "unknown"
    momentum_state:    str          = "unknown"
    rejection_state:   str          = "unknown"
    range_state:       str          = "unknown"
    structure_state:   str          = "unknown"
    sequence_state:    str          = "unknown"
    pattern_tags:      List[str]    = field(default_factory=list)
    momentum_score:    float        = 0.0
    rejection_score:   float        = 0.0
    compression_score: float        = 0.0
    structure_score:   float        = 0.0
    confidence_score:  float        = 0.0
    context_tags:      List[str]    = field(default_factory=list)
    warnings:          List[str]    = field(default_factory=list)
    close:             float        = 0.0
    timestamp:         str          = ""
    symbol:            str          = "GC=F"
    timeframe:         str          = "M5"
    schema_version:    str          = "1.0"
    _raw: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandleInput":
        tags = d.get("pattern_tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split("|") if t.strip()]
        ctx = d.get("context_tags", [])
        if isinstance(ctx, str):
            ctx = [c.strip() for c in ctx.split("|") if c.strip()]
        return cls(
            direction_bias=float(d.get("direction_bias", 0)),
            primary_state=d.get("primary_state", "unknown"),
            momentum_state=d.get("momentum_state", "unknown"),
            rejection_state=d.get("rejection_state", "unknown"),
            range_state=d.get("range_state", "unknown"),
            structure_state=d.get("structure_state", "unknown"),
            sequence_state=d.get("sequence_state", "unknown"),
            pattern_tags=tags,
            momentum_score=float(d.get("momentum_score", 0)),
            rejection_score=float(d.get("rejection_score", 0)),
            compression_score=float(d.get("compression_score", 0)),
            structure_score=float(d.get("structure_score", 0)),
            confidence_score=float(d.get("confidence_score", 0)),
            context_tags=ctx,
            warnings=d.get("warnings", []) if isinstance(d.get("warnings"), list) else [],
            close=float(d.get("close", 0)),
            timestamp=d.get("timestamp", ""),
            symbol=d.get("symbol", "GC=F"),
            timeframe=d.get("timeframe", "M5"),
            schema_version=d.get("schema_version", "1.0"),
            _raw=d,
        )

    @property
    def is_stale(self) -> bool:
        if not self.timestamp:
            return True
        try:
            ts = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
            age = datetime.now(HKT) - ts
            return age.total_seconds() > 30 * 60   # 30-min staleness
        except Exception:
            return False   # if we can't parse, assume OK

    @property
    def is_bearish(self) -> bool:
        return self.direction_bias < -0.2

    @property
    def is_bullish(self) -> bool:
        return self.direction_bias > 0.2


# ── Score dataclasses ─────────────────────────────────────────────────────────

@dataclass
class FusionScores:
    context_score:      float   # 0-100, briefing alignment with trade direction
    price_action_score: float  # 0-100, candlestick quality
    environment_score: float   # 0-100, regime / event / volatility fit
    quality_score:      float   # 0-100, data quality / staleness / conflicts

    def avg(self) -> float:
        return (self.context_score + self.price_action_score +
                self.environment_score + self.quality_score) / 4.0


# ── FusionEngine ──────────────────────────────────────────────────────────────

class FusionEngine:
    """
    Rules-based, read-only confluence engine combining briefing + candlestick
    data into a single decision object for dashboard / report / human review.
    """

    # ── Score weights ───────────────────────────────────────────────────────

    # Context score: how well does briefing bias match candle direction?
    CTX_BIAS_MATCH_WEIGHT    = 0.40
    CTX_STANCE_MATCH_WEIGHT   = 0.25
    CTX_CONFIDENCE_WEIGHT    = 0.20
    CTX_EVENT_RISK_PENALTY   = 0.35  # multiplier when event risk is high

    # Price action score
    PA_DIRECTION_WEIGHT      = 0.30
    PA_MOMENTUM_WEIGHT       = 0.20
    PA_STRUCTURE_WEIGHT      = 0.20
    PA_REJECTION_WEIGHT      = 0.15
    PA_PATTERN_BONUS         = 0.15

    # Environment score
    ENV_REGIME_FIT_WEIGHT    = 0.35
    ENV_CANDLE_ALIGN_WEIGHT  = 0.30
    ENV_VOLATILITY_WEIGHT    = 0.20
    ENV_COMPRESSION_WEIGHT   = 0.15

    # Quality score
    QUAL_STALENESS_PENALTY   = 0.30
    QUAL_MISSING_BRIEF_PENALTY = 30.0
    QUAL_MISSING_CANDLE_PENALTY = 25.0
    QUAL_CONFLICT_PENALTY    = 0.25  # multiplier
    QUAL_HIGH_EVENT_PENALTY  = 0.20

    # Decision thresholds
    CONFLUENCE_THRESHOLD     = 60.0   # min avg score for watch signals
    HIGH_CONFLUENCE          = 75.0   # strong signal
    STRONG_BIAS             = 0.45   # direction_bias for "strong" label
    MODERATE_BIAS           = 0.25   # direction_bias for moderate label

    def __init__(self):
        pass

    def run(
        self,
        briefing: Optional[BriefingInput] = None,
        candle:   Optional[CandleInput]   = None,
    ) -> Dict[str, Any]:
        """
        Run fusion on briefing + candle inputs. Either or both may be None
        (missing inputs reduce quality_score and bias toward wait/no_trade).
        Returns a complete decision dict.
        """
        # ── Normalize inputs ────────────────────────────────────────────────
        b = briefing or BriefingInput()
        c = candle   or CandleInput()

        # ── Compute 4 scores ────────────────────────────────────────────────
        ctx_score   = self._context_score(b, c)
        pa_score    = self._price_action_score(c)
        env_score   = self._environment_score(b, c)
        qual_score  = self._quality_score(b, c)

        scores = FusionScores(
            context_score=ctx_score,
            price_action_score=pa_score,
            environment_score=env_score,
            quality_score=qual_score,
        )

        # ── Detect conflicts ────────────────────────────────────────────────
        conflicts = self._detect_conflicts(b, c)

        # ── Derive bias direction ───────────────────────────────────────────
        bias_dir, bias_strength = self._derive_bias(b, c)

        # ── Determine market regime ─────────────────────────────────────────
        regime = self._derive_regime(b, c)

        # ── Determine risk state ────────────────────────────────────────────
        risk_state = self._derive_risk_state(b, c)

        # ── Compute entry readiness ─────────────────────────────────────────
        entry_readiness = self._derive_entry_readiness(scores, conflicts, b, c)

        # ── Make decision ──────────────────────────────────────────────────
        decision, decision_strength = self._make_decision(
            scores, conflicts, bias_dir, bias_strength,
            entry_readiness, b, c,
        )

        # ── Build reasons list ─────────────────────────────────────────────
        reasons = self._build_reasons(scores, bias_dir, bias_strength,
                                     regime, risk_state, conflicts, b, c)

        # ── Collect warnings ───────────────────────────────────────────────
        warnings = self._collect_warnings(b, c, conflicts, scores)

        # ── Inputs used ─────────────────────────────────────────────────────
        inputs_used = []
        if briefing: inputs_used.append("briefing")
        if candle:   inputs_used.append("candlestick")
        if not inputs_used:
            inputs_used.append("none")

        missing = []
        if not briefing: missing.append("briefing")
        if not candle:   missing.append("candlestick")

        # ── Build final output ──────────────────────────────────────────────
        now = datetime.now(HKT)
        return {
            # Schema identity
            "schema_version":   "1.0",
            "generated_at":     now.isoformat(),
            "timestamp":        now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),

            # Scores
            "context_score":       round(scores.context_score, 1),
            "price_action_score":  round(scores.price_action_score, 1),
            "environment_score":   round(scores.environment_score, 1),
            "quality_score":        round(scores.quality_score, 1),
            "confluence_score":     round(scores.avg(), 1),

            # Direction
            "directional_bias":    bias_dir.value,
            "bias_strength":        bias_strength,

            # Decision
            "decision":            decision.value,
            "decision_strength":   decision_strength,
            "entry_readiness":      entry_readiness.value,

            # Context
            "market_regime":   regime.value,
            "risk_state":      risk_state.value,
            "reasons":         reasons,
            "conflicts":       conflicts,

            # Meta
            "warnings":        warnings,
            "inputs_used":     inputs_used,
            "missing_inputs":  missing,

            # Passthrough key data (for downstream consumers)
            "_briefing_timestamp": b.timestamp_hkt,
            "_candle_timestamp":  c.timestamp,
            "_candle_close":       c.close if candle else None,
            "_candle_direction_bias": c.direction_bias if candle else None,
            "_candle_primary_state": c.primary_state if candle else None,
        }

    # ── Score methods ────────────────────────────────────────────────────────

    def _context_score(self, b: BriefingInput, c: CandleInput) -> float:
        """Context alignment between briefing bias and candle direction."""
        score = 50.0
        has_b = b.market_bias != "unknown" and b.market_bias != ""
        has_c = c.direction_bias != 0.0

        if not has_b and not has_c:
            return 25.0  # no context at all

        # Bias alignment
        if has_b and has_c:
            bullish_b = b.market_bias in ("bullish", "long_bias", "breakout_long",
                                           "cautious_long", "long_only")
            bearish_b = b.market_bias in ("bearish", "short_bias", "breakout_short",
                                            "cautious_short", "short_only")
            bullish_c = c.is_bullish
            bearish_c = c.is_bearish

            if bullish_b and bullish_c:
                score += 35
            elif bearish_b and bearish_c:
                score += 35
            elif bullish_b and bearish_c:
                score -= 20
            elif bearish_b and bullish_c:
                score -= 20
            else:
                score += 5   # at least both neutral-ish

        # Stance alignment
        stance = b.effective_trading_stance
        if stance and stance != "unknown":
            if stance in ("long_bias", "breakout_long") and c.is_bullish:
                score += 15
            elif stance in ("short_bias", "breakout_short") and c.is_bearish:
                score += 15
            elif stance in ("wait", "no_trade", "neutral"):
                score -= 15

        # Confidence
        conf = b.confidence
        if conf > 0:
            score += (conf / 100.0) * 15

        # Event risk penalty
        if b.has_high_event_risk:
            score *= self.CTX_EVENT_RISK_PENALTY

        return max(0.0, min(100.0, score))

    def _price_action_score(self, c: CandleInput) -> float:
        """Candlestick quality and conviction."""
        if c.direction_bias == 0.0 and c.primary_state == "unknown":
            return 30.0

        score = 50.0

        # Direction conviction
        score += abs(c.direction_bias) * 30

        # Momentum
        if c.momentum_state == "accelerating":
            score += 12
        elif c.momentum_state == "decelerating":
            score -= 10

        # Structure
        if c.structure_state in ("higher_high", "higher_low"):
            score += 10
        elif c.structure_state in ("lower_high", "lower_low"):
            score += 10
        elif c.structure_state == "neutral":
            score -= 5

        # Rejection quality
        score += c.rejection_score * 0.12

        # Pattern bonuses
        strong_patterns = {
            "bullish_engulfing", "hammer_like", "momentum_bar_up",
            "bearish_engulfing", "shooting_star_like", "momentum_bar_down",
        }
        pattern_matches = set(c.pattern_tags) & strong_patterns
        score += len(pattern_matches) * 8

        # Compression breakout potential
        if c.range_state == "compressed":
            score += 10   # compression = energy stored

        return max(0.0, min(100.0, score))

    def _environment_score(self, b: BriefingInput, c: CandleInput) -> float:
        """Regime fit, volatility, and event environment."""
        score = 50.0

        # Regime fit: trending in direction of bias is good
        regime = b.volatility_regime or "unknown"
        c_regime = c.range_state or "unknown"
        bias_dir = self._derive_bias(b, c)[0]

        if regime in ("low", "normal"):
            score += 10
        elif regime == "high":
            score -= 10

        if c_regime == "compressed":
            score += 15   # compressed = good for mean-reversion or breakout
        elif c_regime == "expanding":
            score += 8

        # Candle-regime alignment
        if c.range_state == "compressed" and abs(c.direction_bias) > 0.3:
            score += 10   # compressed + directional = strong

        # Event risk
        if b.has_high_event_risk:
            score -= 20

        # Sequence state
        if c.sequence_state in ("exhausting_bull", "exhausting_bear"):
            score -= 15

        return max(0.0, min(100.0, score))

    def _quality_score(self, b: BriefingInput, c: CandleInput) -> float:
        """Data quality, staleness, and conflict penalty."""
        score = 100.0

        # Missing inputs
        if not b.timestamp_hkt:
            score -= self.QUAL_MISSING_BRIEF_PENALTY
        if not c.timestamp:
            score -= self.QUAL_MISSING_CANDLE_PENALTY

        # Staleness
        if b.is_stale:
            score *= (1.0 - self.QUAL_STALENESS_PENALTY)
        if c.is_stale:
            score *= (1.0 - self.QUAL_STALENESS_PENALTY * 0.5)

        # Conflicts
        conflicts = self._detect_conflicts(b, c)
        if conflicts:
            score *= (1.0 - self.QUAL_CONFLICT_PENALTY * min(len(conflicts), 3) / 3)

        # High event risk
        if b.has_high_event_risk:
            score *= (1.0 - self.QUAL_HIGH_EVENT_PENALTY)

        # Market closed
        if b.is_market_closed:
            score *= 0.5

        return max(0.0, min(100.0, score))

    # ── Conflict detection ───────────────────────────────────────────────────

    def _detect_conflicts(self, b: BriefingInput, c: CandleInput) -> List[str]:
        """Identify contradictions between inputs."""
        conflicts: List[str] = []

        # Bias conflict
        bullish_b = b.market_bias in ("bullish", "long_bias", "breakout_long",
                                       "cautious_long", "long_only")
        bearish_b = b.market_bias in ("bearish", "short_bias", "breakout_short",
                                       "cautious_short", "short_only")
        if bullish_b and c.is_bearish:
            conflicts.append("briefing_bullish_candle_bearish")
        elif bearish_b and c.is_bullish:
            conflicts.append("briefing_bearish_candle_bullish")

        # Stance vs direction
        stance = b.effective_trading_stance
        if stance in ("no_trade", "neutral_pre_ny") and abs(c.direction_bias) > 0.3:
            conflicts.append("stance_no_trade_candle_directional")

        # Momentum vs direction
        if c.momentum_state == "decelerating" and c.is_bullish:
            conflicts.append("momentum_decelerating_but_bullish_bias")
        elif c.momentum_state == "accelerating" and c.is_bearish:
            conflicts.append("momentum_accelerating_but_bearish_bias")

        # Rejection conflict
        if c.rejection_state == "rejecting_high" and c.is_bullish:
            conflicts.append("rejecting_high_but_bullish")
        if c.rejection_state == "rejecting_low" and c.is_bearish:
            conflicts.append("rejecting_low_but_bearish")

        # Range vs directional
        if c.range_state == "compressed" and c.primary_state not in ("compressed", "neutral"):
            pass   # compression + directional is fine
        if abs(c.direction_bias) > 0.5 and c.range_state == "compressed":
            pass   # directional + compressed = not a conflict

        # Structure conflict
        if c.structure_state == "lower_low" and c.is_bullish and c.direction_bias > 0.4:
            conflicts.append("lower_low_but_strong_bullish")

        return conflicts

    # ── Bias derivation ─────────────────────────────────────────────────────

    def _derive_bias(self, b: BriefingInput, c: CandleInput) -> tuple:
        """Return (BiasDirection, strength_label)."""
        b_bull = b.market_bias in ("bullish", "long_bias", "breakout_long",
                                   "cautious_long", "long_only")
        b_bear = b.market_bias in ("bearish", "short_bias", "breakout_short",
                                    "cautious_short", "short_only")
        c_bull = c.is_bullish
        c_bear = c.is_bearish

        b_score = (b_bull or 0) - (b_bear or 0)
        c_score = int(c_bull) - int(c_bear)
        total = b_score + c_score

        if total >= 2:
            strength = "strong" if c.direction_bias > self.STRONG_BIAS else "moderate"
            return BiasDirection.BULLISH, strength
        elif total <= -2:
            strength = "strong" if c.direction_bias < -self.STRONG_BIAS else "moderate"
            return BiasDirection.BEARISH, strength
        elif total == 0:
            return BiasDirection.MIXED, "none"
        else:
            return BiasDirection.NEUTRAL, "weak"

    # ── Regime ───────────────────────────────────────────────────────────────

    def _derive_regime(self, b: BriefingInput, c: CandleInput) -> MarketRegime:
        v = b.volatility_regime or "unknown"
        r = c.range_state or "unknown"
        seq = c.sequence_state or "unknown"
        bias = c.direction_bias

        if v == "high":
            return MarketRegime.VOLATILE
        if r == "compressed":
            return MarketRegime.COMPRESSED
        if abs(bias) > 0.4 and c.structure_state in ("higher_high", "lower_low"):
            if bias > 0:
                return MarketRegime.TRENDING_UP
            return MarketRegime.TRENDING_DOWN
        return MarketRegime.RANGING

    # ── Risk state ───────────────────────────────────────────────────────────

    def _derive_risk_state(self, b: BriefingInput, c: CandleInput) -> RiskState:
        risk = 50.0

        if b.has_high_event_risk:
            risk += 25
        if b.is_market_closed:
            risk += 10
        if c.range_state == "expanding":
            risk += 15
        if c.sequence_state in ("exhausting_bull", "exhausting_bear"):
            risk += 15
        if c.compression_score > 70:
            risk += 5   # compressed = eventually expands = risk
        if b.volatility_regime == "high":
            risk += 20

        if risk >= 85:
            return RiskState.EXTREME
        elif risk >= 70:
            return RiskState.HIGH
        elif risk >= 55:
            return RiskState.MODERATE
        elif risk >= 40:
            return RiskState.LOW
        return RiskState.UNKNOWN

    # ── Entry readiness ──────────────────────────────────────────────────────

    def _derive_entry_readiness(
        self,
        scores: FusionScores,
        conflicts: List[str],
        b: BriefingInput,
        c: CandleInput,
    ) -> EntryReadiness:
        avg = scores.avg()
        if avg >= self.HIGH_CONFLUENCE and not conflicts:
            return EntryReadiness.READY
        elif avg >= self.CONFLUENCE_THRESHOLD and len(conflicts) <= 1:
            return EntryReadiness.CONDITIONAL
        return EntryReadiness.NOT_READY

    # ── Decision ─────────────────────────────────────────────────────────────

    def _make_decision(
        self,
        scores: FusionScores,
        conflicts: List[str],
        bias_dir: BiasDirection,
        bias_strength: str,
        entry_readiness: EntryReadiness,
        b: BriefingInput,
        c: CandleInput,
    ) -> tuple:
        """
        Decision priority (highest first):
        1. no_trade  — hard blocks
        2. wait      — mixed / conflicts / insufficient confluence
        3. long_watch / short_watch — bullish / bearish alignment + confluence
        """

        avg = scores.avg()
        qual = scores.quality_score

        # ── Hard blocks → no_trade ──────────────────────────────────────────
        # Market closed
        if b.is_market_closed:
            return Decision.NO_TRADE, "hard_block_market_closed"

        # Very poor quality
        if qual < 25:
            return Decision.NO_TRADE, "hard_block_data_quality"

        # Briefing says no_trade stance
        stance = b.effective_trading_stance
        if stance in ("no_trade", "neutral"):
            return Decision.NO_TRADE, "hard_block_stance_no_trade"

        # Stale briefing AND stale candle
        if b.is_stale and c.is_stale:
            return Decision.NO_TRADE, "hard_block_both_stale"

        # Missing both inputs
        if not b.timestamp_hkt and not c.timestamp:
            return Decision.NO_TRADE, "hard_block_no_inputs"

        # Extreme risk
        risk = self._derive_risk_state(b, c)
        if risk == RiskState.EXTREME:
            return Decision.NO_TRADE, "hard_block_extreme_risk"

        # ── Conflicts / insufficient confluence → wait ──────────────────────
        if len(conflicts) >= 3:
            return Decision.WAIT, "conflict_too_many"

        if conflicts and avg < self.CONFLUENCE_THRESHOLD:
            return Decision.WAIT, "conflict_insufficient_confluence"

        if entry_readiness == EntryReadiness.NOT_READY and avg < self.CONFLUENCE_THRESHOLD:
            return Decision.WAIT, "insufficient_confluence"

        # Mixed bias
        if bias_dir == BiasDirection.MIXED:
            return Decision.WAIT, "mixed_bias"

        # Neutral with conflicts
        if bias_dir == BiasDirection.NEUTRAL and conflicts:
            return Decision.WAIT, "neutral_with_conflicts"

        # No directional conviction
        if abs(c.direction_bias) < 0.15:
            return Decision.WAIT, "insufficient_directional_conviction"

        # ── Watch decisions ─────────────────────────────────────────────────
        if bias_dir == BiasDirection.BULLISH and avg >= 45:
            strength = "strong" if avg >= self.HIGH_CONFLUENCE else "moderate"
            return Decision.LONG_WATCH, strength

        if bias_dir == BiasDirection.BEARISH and avg >= 45:
            strength = "strong" if avg >= self.HIGH_CONFLUENCE else "moderate"
            return Decision.SHORT_WATCH, strength

        # Fallback
        return Decision.WAIT, "fallback_insufficient_signal"

    # ── Reasons ───────────────────────────────────────────────────────────────

    def _build_reasons(
        self,
        scores: FusionScores,
        bias_dir: BiasDirection,
        bias_strength: str,
        regime: MarketRegime,
        risk_state: RiskState,
        conflicts: List[str],
        b: BriefingInput,
        c: CandleInput,
    ) -> List[str]:
        reasons: List[str] = []

        # Direction
        if bias_dir == BiasDirection.BULLISH:
            reasons.append(f"bullish_alignment:{c.direction_bias:+.2f}")
        elif bias_dir == BiasDirection.BEARISH:
            reasons.append(f"bearish_alignment:{c.direction_bias:+.2f}")
        elif bias_dir == BiasDirection.MIXED:
            reasons.append("mixed_directional_signals")

        # Scores
        reasons.append(f"confluence_score:{scores.avg():.1f}")
        if scores.context_score > 65:
            reasons.append("strong_context_alignment")
        if scores.price_action_score > 70:
            reasons.append("strong_price_action")
        if scores.environment_score < 40:
            reasons.append("weak_environment")

        # Regime
        reasons.append(f"regime:{regime.value}")

        # Structure
        if c.structure_state not in ("unknown", ""):
            reasons.append(f"structure:{c.structure_state}")
        if c.pattern_tags:
            reasons.append(f"patterns:{','.join(c.pattern_tags[:3])}")

        # Rejection
        if c.rejection_state not in ("neutral", "unknown"):
            reasons.append(f"rejection:{c.rejection_state}")

        # Risks
        if risk_state in (RiskState.HIGH, RiskState.EXTREME):
            reasons.append(f"risk_state:{risk_state.value}")
        if b.has_high_event_risk:
            reasons.append("high_event_risk")

        # Conflicts
        for cf in conflicts:
            reasons.append(f"conflict:{cf}")

        return reasons

    # ── Warnings ─────────────────────────────────────────────────────────────

    def _collect_warnings(
        self,
        b: BriefingInput,
        c: CandleInput,
        conflicts: List[str],
        scores: FusionScores,
    ) -> List[str]:
        warnings: List[str] = []

        if b.is_stale:
            warnings.append("briefing_stale")
        if c.is_stale:
            warnings.append("candlestick_stale")
        if not b.timestamp_hkt:
            warnings.append("briefing_missing")
        if not c.timestamp:
            warnings.append("candlestick_missing")
        if b.has_high_event_risk:
            warnings.append("high_event_risk")
        if b.is_market_closed:
            warnings.append("market_closed")
        if scores.quality_score < 50:
            warnings.append("low_data_quality")
        if scores.context_score < 35:
            warnings.append("weak_context_alignment")
        for cf in conflicts:
            warnings.append(f"conflict_{cf}")

        return warnings