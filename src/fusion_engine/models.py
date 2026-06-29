"""
V4 Fusion Engine — models (dataclasses + enums).

Design:
  • Enums are independent from candlestick_engine.* (per M5 confirmation #5).
  • FusionInput is the *normalized* snapshot of upstream signals; built by
    `mapper.build_fusion_input()` from EngineOutput / Briefing payload dicts.
  • FusionOutput is the new top-level contract, schema_version="4.0".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Enums (independent, frozen string sets for JSON safety) ─────────────────


class ConsensusLabel:
    ALIGNED              = "aligned"
    PARTIALLY_ALIGNED    = "partially_aligned"
    MIXED                = "mixed"
    INSUFFICIENT_CONTEXT = "insufficient_context"

    ALL = (
        ALIGNED,
        PARTIALLY_ALIGNED,
        MIXED,
        INSUFFICIENT_CONTEXT,
    )


class ConflictLabel:
    NONE                   = "none"
    COUNTER_TREND          = "counter_trend"
    MACRO_TECHNICAL_CONFLICT = "macro_technical_conflict"
    DATA_QUALITY_ISSUE     = "data_quality_issue"
    MISSING_BRIEFING       = "missing_briefing"

    ALL = (
        NONE,
        COUNTER_TREND,
        MACRO_TECHNICAL_CONFLICT,
        DATA_QUALITY_ISSUE,
        MISSING_BRIEFING,
    )


# ── Input: normalized snapshot of upstream signals ──────────────────────────


@dataclass
class FusionInput:
    """Snapshot of upstream signals fed into FusionEngine.fuse().

    Fields are intentionally loose (Optional, with defaults) so we can build a
    FusionInput even when briefing is absent (graceful degradation).

    Attributes:
        candle_output:    candlestick EngineOutput (M5 schema).
        briefing_payload: dict-shaped briefing payload OR None.
        cfg:              engine config (weights, thresholds). Optional;
                          FusionEngine applies defaults if None.
    """

    candle_output: "object"   # typed as object to avoid circular import;
                              # runtime check via duck-typing.
    briefing_payload: Optional[dict] = None
    cfg: Optional["object"] = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Convenience: which input vectors are present?
    @property
    def has_briefing(self) -> bool:
        return bool(self.briefing_payload)

    @property
    def has_candlestick(self) -> bool:
        return self.candle_output is not None


# ── Output: FusionOutput dataclass ───────────────────────────────────────────


@dataclass
class FusionOutput:
    """Unified V4 Fusion decision contract. Schema version: 4.0.

    Top-level mirrors V3 M5 Execution-Ready Schema (engine_name, signal_id,
    decision_ready, trade_eligible, execution_*).  Fusion adds:
      fusion_bias, fusion_confidence, consensus_label, conflict_label,
      trade_candidate, source_payload (fusion-specific breakdown).

    Field shape is JSON-serialisable; to_dict / from_dict make this stable.
    """

    # Identity
    engine_name:    str = "fusion"
    schema_version: str = "4.0"
    run_id:         str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    signal_id:      str = ""
    symbol:         str = "XAUUSD"
    timestamp:      str = field(default_factory=lambda: FusionOutput._now())
    timeframe:      str = "1D"

    # Decision
    fusion_bias:        str = "neutral"   # bullish | bearish | neutral
    fusion_confidence:  float = 0.0
    consensus_label:    str = ConsensusLabel.INSUFFICIENT_CONTEXT
    conflict_label:     str = ConflictLabel.MISSING_BRIEFING
    trade_candidate:    bool = False

    # Execution-ready (passes through V3 M5 schema)
    decision_ready:   bool = False
    trade_eligible:   bool = False
    execution_status: str = "not_sent"
    execution_mode:   str = "none"
    execution_intent: dict = field(default_factory=dict)

    # Free-form zh text + raw payload
    explanation_zh: str = ""
    source_payload: dict = field(default_factory=dict)

    # ── Computed properties (mirror EngineOutput) ──────────────────────────

    @property
    def is_bullish(self) -> bool:
        return self.fusion_bias == "bullish"

    @property
    def is_bearish(self) -> bool:
        return self.fusion_bias == "bearish"

    @property
    def is_neutral(self) -> bool:
        return self.fusion_bias == "neutral"

    @property
    def has_consensus(self) -> bool:
        return self.consensus_label == ConsensusLabel.ALIGNED

    @property
    def has_conflict(self) -> bool:
        return self.conflict_label != ConflictLabel.NONE

    @property
    def run_id_short(self) -> str:
        return self.run_id[:8]

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "engine_name":       self.engine_name,
            "schema_version":    self.schema_version,
            "run_id":            self.run_id,
            "signal_id":         self.signal_id,
            "symbol":            self.symbol,
            "timestamp":         self.timestamp,
            "timeframe":         self.timeframe,
            "fusion_bias":       self.fusion_bias,
            "fusion_confidence": round(self.fusion_confidence, 4),
            "consensus_label":   self.consensus_label,
            "conflict_label":    self.conflict_label,
            "trade_candidate":   self.trade_candidate,
            "decision_ready":    self.decision_ready,
            "trade_eligible":    self.trade_eligible,
            "execution_status":  self.execution_status,
            "execution_mode":    self.execution_mode,
            "execution_intent":  self.execution_intent,
            "explanation_zh":    self.explanation_zh,
            "source_payload":    self.source_payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FusionOutput":
        return cls(
            engine_name       = data.get("engine_name", "fusion"),
            schema_version    = data.get("schema_version", "4.0"),
            run_id            = data.get("run_id", uuid.uuid4().hex[:12]),
            signal_id         = data.get("signal_id", ""),
            symbol            = data.get("symbol", "XAUUSD"),
            timestamp         = data.get("timestamp", cls._now()),
            timeframe         = data.get("timeframe", "1D"),
            fusion_bias       = data.get("fusion_bias", "neutral"),
            fusion_confidence = data.get("fusion_confidence", 0.0),
            consensus_label   = data.get(
                "consensus_label", ConsensusLabel.INSUFFICIENT_CONTEXT
            ),
            conflict_label    = data.get(
                "conflict_label", ConflictLabel.MISSING_BRIEFING
            ),
            trade_candidate   = data.get("trade_candidate", False),
            decision_ready    = data.get("decision_ready", False),
            trade_eligible    = data.get("trade_eligible", False),
            execution_status  = data.get("execution_status", "not_sent"),
            execution_mode    = data.get("execution_mode", "none"),
            execution_intent  = data.get("execution_intent", {}),
            explanation_zh    = data.get("explanation_zh", ""),
            source_payload    = data.get("source_payload", {}),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
