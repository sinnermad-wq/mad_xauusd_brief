"""
V4 Fusion Engine — orchestrator.

FusionEngine.fuse() reads a FusionInput, applies the pure rule functions from
rules.py, and emits a FusionOutput. No I/O — pure orchestration.

Casual call:
    engine = FusionEngine()
    fusion = engine.fuse(fusion_input)

The engine accepts an optional `cfg` (fusion_engine.rules.FusionConfig). When
absent it uses defaults (Confirm #1, #2, #4).
"""

from __future__ import annotations

import uuid
from typing import Optional

from .models import ConflictLabel, ConsensusLabel, FusionInput, FusionOutput
from .rules import (
    DEFAULT_WEIGHTS,
    FusionConfig,
    agreement_score,
    briefing_score,
    candlestick_score,
    classify_conflict,
    classify_consensus,
    compute_fusion_confidence,
    quality_score,
)
from .mapper import read_briefing_snapshot, read_candlestick_snapshot


class FusionEngine:
    """Stateless decision-layer engine. Construct once, call .fuse() many times."""

    def __init__(self, cfg: Optional[FusionConfig] = None):
        self.cfg = cfg or FusionConfig(weights=dict(DEFAULT_WEIGHTS))

    # ── Public entry point ───────────────────────────────────────────────

    def fuse(self, fusion_input: FusionInput) -> FusionOutput:
        """Run the rule-based fusion; return a fully-populated FusionOutput."""
        candle = fusion_input.candle_output
        candle_snap = read_candlestick_snapshot(candle)
        briefing_snap = read_briefing_snapshot(fusion_input.briefing_payload)

        # ── Step 1: per-signal scores ─────────────────────────────────────
        cs = candlestick_score(
            candle_bias=candle_snap["bias"],
            candlestick_confidence=candle_snap["confidence"],
            candle_validation_status=candle_snap["validation_status"],
            candle_trade_eligible=candle_snap["trade_eligible"],
        )
        br = briefing_score(
            briefing_bias=briefing_snap["bias"],
            briefing_confidence=briefing_snap["confidence"],
            briefing_present=briefing_snap["present"],
            min_floor=self.cfg.min_briefing_confidence_floor,
        )
        ag = agreement_score(
            candle_bias=candle_snap["bias"],
            briefing_bias=briefing_snap["bias"],
            briefing_present=briefing_snap["present"],
        )
        qu = quality_score(
            candle_validation_status=candle_snap["validation_status"],
            candle_data_quality_flag=candle_snap["data_quality_flag"],
            briefing_present=briefing_snap["present"],
        )

        # ── Step 2: consensus + conflict labels ───────────────────────────
        consensus = classify_consensus(
            candle_bias=candle_snap["bias"],
            briefing_bias=briefing_snap["bias"],
            briefing_present=briefing_snap["present"],
        )
        conflict = classify_conflict(
            candle_validation_status=candle_snap["validation_status"],
            candle_data_quality_flag=candle_snap["data_quality_flag"],
            briefing_present=briefing_snap["present"],
            consensus_label=consensus,
        )

        # ── Step 3: final fusion confidence (weighted + caps) ──────────────
        fusion_conf = compute_fusion_confidence(
            cs_score=cs, br_score=br, ag_score=ag, qu_score=qu,
            conflict_label=conflict, cfg=self.cfg,
        )

        # ── Step 4: fusion bias decision ──────────────────────────────────
        fusion_bias = _decide_fusion_bias(
            candle_bias=candle_snap["bias"],
            briefing_bias=briefing_snap["bias"] if briefing_snap["present"] else None,
            consensus=consensus,
        )

        # ── Step 5: trade_candidate gate ──────────────────────────────────
        trade_candidate = (
            consensus == ConsensusLabel.ALIGNED
            and conflict == ConflictLabel.NONE
            and fusion_conf >= self.cfg.min_confidence_for_trade
            and candle_snap["trade_eligible"]
        )

        # ── Step 6: build execution_intent ────────────────────────────────
        intent = _build_execution_intent(
            fusion_bias=fusion_bias,
            fusion_conf=fusion_conf,
            consensus=consensus,
            conflict=conflict,
            trade_candidate=trade_candidate,
            candle_snap=candle_snap,
            briefing_snap=briefing_snap,
            candle_scores={"cs": cs, "br": br, "ag": ag, "qu": qu},
            run_id=fusion_input.run_id,
        )

        # ── Step 7: explanation_zh ────────────────────────────────────────
        explanation_zh = _build_explanation_zh(
            fusion_bias=fusion_bias,
            fusion_conf=fusion_conf,
            consensus=consensus,
            conflict=conflict,
            trade_candidate=trade_candidate,
            briefing_present=briefing_snap["present"],
            candle_snap=candle_snap,
            briefing_snap=briefing_snap,
        )

        # ── Step 8: extended V5 fields ────────────────────────────────────
        # regime_tag: derived from consensus + candle structure
        regime_tag = _derive_regime_tag(consensus, candle_snap)

        # execution_intent_str: "long_only"|"short_only"|"both"|"none"
        execution_intent_str = _derive_execution_intent_str(fusion_bias, trade_candidate)

        # signal_strength: derived from fusion_confidence bands
        signal_strength = _derive_signal_strength(fusion_conf)

        # summary_zh: narrator/briefing output → fallback to explanation_zh
        summary_zh = _derive_summary_zh(briefing_snap, explanation_zh)

        # ── Step 9: assemble FusionOutput ─────────────────────────────────
        return FusionOutput(
            engine_name="fusion",
            schema_version="4.0",
            run_id=fusion_input.run_id or uuid.uuid4().hex[:12],
            signal_id=_derive_signal_id(
                candle_signal_id=candle_snap.get(
                    "execution_intent", {}
                ).get("strategy_id"),
                run_id=fusion_input.run_id,
            ),
            symbol=candle_snap["symbol"],
            timestamp=candle_snap["timestamp"] or FusionOutput._now(),
            timeframe=candle_snap["timeframe"],

            fusion_bias=fusion_bias,
            fusion_confidence=fusion_conf,
            consensus_label=consensus,
            conflict_label=conflict,
            trade_candidate=trade_candidate,

            decision_ready=trade_candidate,
            trade_eligible=trade_candidate,
            execution_status="not_sent",
            execution_mode="none",
            execution_intent=intent,
            execution_intent_str=execution_intent_str,

            signal_strength=signal_strength,
            regime_tag=regime_tag,
            invalidation_reason=None,          # Phase 1: always null
            summary_zh=summary_zh,

            explanation_zh=explanation_zh,
            source_payload={
                "inputs": {
                    "candlestick": candle_snap,
                    "briefing":   briefing_snap,
                },
                "scores": {
                    "candlestick_score":  cs,
                    "briefing_score":     br,
                    "agreement_score":    ag,
                    "quality_score":      qu,
                    "final_fusion_confidence": fusion_conf,
                },
                "weights_applied": self.cfg.normalized(),
                "labels": {
                    "consensus": consensus,
                    "conflict":  conflict,
                },
                "trade_candidate": trade_candidate,
                "thresholds": {
                    "counter_trend_cap": self.cfg.counter_trend_cap,
                    "min_confidence_for_trade": self.cfg.min_confidence_for_trade,
                },
            },
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _decide_fusion_bias(*, candle_bias, briefing_bias, consensus: str) -> str:
    """Rule-based bias decision.

    Rules:
      • consensus == ALIGNED                     → that direction
      • consensus == PARTIALLY_ALIGNED           → directional side wins
      • consensus == INSUFFICIENT_CONTEXT        → candle_bias (Confirm #4)
      • consensus == MIXED                       → neutral (counter-trend)
      • candle unknown / brief unknown           → neutral
    """
    candle_b = (candle_bias or "").lower()
    brief_b = (briefing_bias or "").lower() if briefing_bias else None

    if consensus == ConsensusLabel.INSUFFICIENT_CONTEXT:
        return candle_b if candle_b in ("bullish", "bearish") else "neutral"

    if consensus == ConsensusLabel.ALIGNED:
        return candle_b or brief_b or "neutral"

    if consensus == ConsensusLabel.PARTIALLY_ALIGNED:
        directional = candle_b if candle_b in ("bullish", "bearish") else brief_b
        return directional if directional in ("bullish", "bearish") else "neutral"

    # MIXED = true opposite → be conservative: neutral
    return "neutral"


def _build_execution_intent(
    *,
    fusion_bias: str,
    fusion_conf: float,
    consensus: str,
    conflict: str,
    trade_candidate: bool,
    candle_snap: dict,
    briefing_snap: dict,
    candle_scores: dict,
    run_id: str,
) -> dict:
    """Fusion-owned execution_intent.

    Conservative when trade_candidate=False (Confirm #5):
      decision = "none"
      reason_codes cleared/expanded with safety reasons.

    Adheres to V3 M5 schema: only contract fields, no broker-specific data.
    """
    reason_codes: list[str] = []
    reason_codes.append(f"consensus:{consensus}")
    reason_codes.append(f"conflict:{conflict}")
    if candle_snap["trade_eligible"]:
        reason_codes.append("candle_trade_eligible:true")
    else:
        reason_codes.append("candle_trade_eligible:false")

    decision = {
        "bullish": "long",
        "bearish": "short",
        "neutral": "flat",
    }.get(fusion_bias, "none")

    if not trade_candidate:
        decision = "none"
        reason_codes.extend([
            f"fusion_conf:{fusion_conf:.2f}",
            "fusion_trade_candidate:false",
            "no_broker_action",
        ])

    return {
        "symbol":       candle_snap["symbol"],
        "decision":     decision,
        "confidence":   round(fusion_conf, 4),
        "strategy_id":  "fusion_v1",
        "timeframe":    candle_snap["timeframe"],
        "entry_type":   None,
        "stop_loss":    None,
        "take_profit":  None,
        "max_risk_pct": None,
        "reason_codes": reason_codes,
    }


def _build_explanation_zh(
    *,
    fusion_bias: str,
    fusion_conf: float,
    consensus: str,
    conflict: str,
    trade_candidate: bool,
    briefing_present: bool,
    candle_snap: dict,
    briefing_snap: dict,
) -> str:
    """Concise zh explanation for Telegram / dashboard / report."""
    bias_zh = {
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性",
    }.get(fusion_bias, "中性")

    parts: list[str] = []
    parts.append(f"融合訊號：{bias_zh}（信心 {fusion_conf:.0%}）")

    if not briefing_present:
        parts.append("宏觀簡報缺席，僅依技術面判斷。")
    else:
        brief_b = briefing_snap.get("bias") or "中性"
        brief_zh = {
            "bullish": "偏多", "bearish": "偏空", "neutral": "中性"
        }.get(brief_b, "中性")
        parts.append(f"技術 vs 宏觀：技術 {bias_zh}，宏觀 {brief_zh}。")

    consensus_zh = {
        ConsensusLabel.ALIGNED:              "一致",
        ConsensusLabel.PARTIALLY_ALIGNED:    "部分一致",
        ConsensusLabel.MIXED:                "分歧",
        ConsensusLabel.INSUFFICIENT_CONTEXT: "資料不足",
    }.get(consensus, "未知")
    parts.append(f"共識狀態：{consensus_zh}。")

    if conflict != ConflictLabel.NONE:
        conflict_zh = {
            ConflictLabel.COUNTER_TREND:            "趨勢反向",
            ConflictLabel.MACRO_TECHNICAL_CONFLICT: "宏觀/技術衝突",
            ConflictLabel.DATA_QUALITY_ISSUE:       "資料品質問題",
            ConflictLabel.MISSING_BRIEFING:         "宏觀資料缺失",
        }.get(conflict, conflict)
        parts.append(f"衝突警示：{conflict_zh}。")

    if trade_candidate:
        parts.append("已通過 trade candidate 門檻（仍為研究輸出，未送出）。")
    else:
        parts.append("未達 trade candidate 條件，暫停執行動作。")

    return " ".join(parts)


def _derive_signal_id(*, candle_signal_id: Optional[str], run_id: str) -> str:
    """Derive a fusion-level signal_id. Reuse candle-side hint if available."""
    if candle_signal_id and candle_signal_id.startswith("sig-"):
        return f"fus-{run_id[:8]}-{candle_signal_id[4:]}"
    return f"fus-{run_id[:8]}"


def _derive_regime_tag(consensus: str, candle_snap: dict) -> str:
    """Derive regime_tag from consensus label + candle structure_state.

    Logic:
      • ALIGNED + trending structure  → "trending"
      • ALIGNED + range structure     → "range"
      • MIXED                         → "volatile"
      • INSUFFICIENT_CONTEXT          → "unknown"
      • counter-trend conflict         → "volatile"
    """
    structure = candle_snap.get("structure_state", "") or ""

    if consensus == ConsensusLabel.MIXED:
        return "volatile"
    if consensus == ConsensusLabel.INSUFFICIENT_CONTEXT:
        return "unknown"

    if structure in ("uptrend", "downtrend"):
        return "trending"
    if structure in ("range", "transition"):
        return "range"

    # PARTIALLY_ALIGNED or ALIGNED with unknown structure
    return "unknown"


def _derive_execution_intent_str(fusion_bias: str, trade_candidate: bool) -> Optional[str]:
    """Map fusion_bias + trade_candidate to execution_intent_str.

    Returns:
      bullish + trade_candidate → "long_only"
      bearish + trade_candidate → "short_only"
      neutral + trade_candidate → "both"
      not trade_candidate       → "none"
    """
    if not trade_candidate:
        return "none"
    mapping = {
        "bullish": "long_only",
        "bearish": "short_only",
        "neutral": "both",
    }
    return mapping.get(fusion_bias, "none")


def _derive_signal_strength(fusion_conf: float) -> str:
    """Derive signal_strength label from fusion_confidence value."""
    if fusion_conf >= 0.75:
        return "strong"
    if fusion_conf >= 0.50:
        return "moderate"
    if fusion_conf >= 0.25:
        return "weak"
    return "unknown"


def _derive_summary_zh(briefing_snap: dict, explanation_zh: str) -> str:
    """Extract summary_zh from briefing payload, fallback to explanation_zh.

    Briefing payload may contain 'summary_zh' (from narrator V4) or
    'final_summary' (legacy briefing output).
    If neither found, return explanation_zh as safe fallback.
    """
    # Primary: narrator-produced summary (added to briefing in future)
    s = briefing_snap.get("summary_zh") if briefing_snap.get("present") else None
    if s:
        return s[:500]  # hard cap at 500 chars

    # Legacy: final_summary from format_report
    s = briefing_snap.get("final_summary") if briefing_snap.get("present") else None
    if s:
        return s[:500]

    # Safe fallback — explanation_zh is always available
    return explanation_zh
