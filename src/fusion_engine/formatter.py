"""
V4 Fusion Engine — Telegram formatter.

Renders FusionOutput as a concise zh-Telegram-friendly text block,
mirroring the Candlestick formatter style (V3 M3).

Layout:
  • Fusion header with bias + consensus emoji
  • Confidence line
  • Consensus / conflict summary
  • Trade candidate status
  • Reason codes
"""

from __future__ import annotations

from .models import FusionOutput, ConsensusLabel, ConflictLabel


_BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
_CONSENSUS_EMOJI = {
    ConsensusLabel.ALIGNED: "✅",
    ConsensusLabel.PARTIALLY_ALIGNED: "🟡",
    ConsensusLabel.MIXED: "⚠️",
    ConsensusLabel.INSUFFICIENT_CONTEXT: "❓",
}
_CONFLICT_EMOJI = {
    ConflictLabel.NONE: "",
    ConflictLabel.COUNTER_TREND: "🚨 counter_trend",
    ConflictLabel.MACRO_TECHNICAL_CONFLICT: "⚔️ marco-tech 衝突",
    ConflictLabel.DATA_QUALITY_ISSUE: "⚠️ 資料品質問題",
    ConflictLabel.MISSING_BRIEFING: "❓ briefing 缺失",
}


def format_fusion_telegram_zh(output: FusionOutput) -> str:
    """Format a FusionOutput for Telegram (zh)."""
    bias_emoji = _BIAS_EMOJI.get(output.fusion_bias, "⚪")
    consensus_emoji = _CONSENSUS_EMOJI.get(output.consensus_label, "•")
    conflict_text = _CONFLICT_EMOJI.get(output.conflict_label, "") or ""

    intent = output.execution_intent or {}
    reason_codes = intent.get("reason_codes", []) or []

    cand = "✓ eligible" if output.trade_eligible else "✗ blocked"
    lines = [
        f"🧠 *XAUUSD Fusion* — {bias_emoji} {output.fusion_bias.upper()}",
        f"confidence: *{output.fusion_confidence:.0%}*",
        f"{consensus_emoji} 共識: {output.consensus_label}",
    ]
    if conflict_text and output.conflict_label != ConflictLabel.NONE:
        lines.append(f"衝突: {conflict_text}")

    lines.append(f"trade: {cand}")
    if reason_codes:
        lines.append("reason:")
        for c in reason_codes[:6]:
            lines.append(f"  • {c}")
    if output.explanation_zh:
        lines.append("")
        lines.append(output.explanation_zh)

    lines.append(f"\n_run_id: `{output.run_id[:8]}` · schema `{output.schema_version}`_")
    return "\n".join(lines)
