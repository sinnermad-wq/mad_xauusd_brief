"""
V4 Fusion Engine — unified decision layer.

Combines:
  • Briefing Engine output (macro / news / regime)
  • Candlestick Engine output (technical analysis)
  • Validation Layer payload (M4 confidence sub-scores / status)

Outputs:
  • FusionOutput contract (additive, schema-versioned)
  • rule-based fusion_bias / fusion_confidence / trade_candidate
  • final execution_intent owned by Fusion (candlestick's intent kept as base only)

Design principles:
  • Pure functions where possible (rules.py scores are deterministic).
  • Backward-compatible inputs: works when briefing is absent.
  • No broker / no live-trading — produces a *contract* only.

Modules:
  models   → dataclasses + enums (Consensus, Conflict, etc.)
  rules    → pure scoring helpers (consensus, conflict, quality, agreement)
  engine   → FusionEngine — orchestrates rules and produces FusionOutput
  mapper   → input adapters: from EngineOutput / Briefing payload → FusionInput
"""

from .models import (
    ConflictLabel,
    ConsensusLabel,
    FusionInput,
    FusionOutput,
)
from .engine import FusionEngine
from .io import write_fusion_record
from .mapper import build_fusion_input
from .rules import (
    agreement_score,
    briefing_score,
    candlestick_score,
    quality_score,
    classify_conflict,
    classify_consensus,
    compute_fusion_confidence,
)

__all__ = [
   "ConsensusLabel",
   "ConflictLabel",
   "FusionInput",
   "FusionOutput",
   "FusionEngine",
   "build_fusion_input",
   "write_fusion_record",
   "agreement_score",
    "briefing_score",
    "candlestick_score",
    "quality_score",
    "classify_conflict",
    "classify_consensus",
    "compute_fusion_confidence",
]

__version__ = "4.0"
