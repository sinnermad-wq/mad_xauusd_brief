"""
Candlestick Engine → EngineOutput Mapper
V3 M2 — Integration Layer

Converts CandleAnalysis (M1 output) to the unified EngineOutput contract.
This is the ONLY place where candlestick-specific field names are mapped to the contract.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .contract import EngineOutput
from .engine import CandleEngine
from .models import CandleAnalysis, BiasDirection

logger = logging.getLogger(__name__)

# ── Bias mapping ─────────────────────────────────────────────────────────────

_BIAS_MAP = {
    BiasDirection.BULLISH: "bullish",
    BiasDirection.BEARISH: "bearish",
    BiasDirection.NEUTRAL: "neutral",
}

_STRENGTH_MAP = {
    BiasDirection.BULLISH: 0.75,
    BiasDirection.BEARISH: 0.75,
    BiasDirection.NEUTRAL: 0.50,
}


def _map_bias(analysis: CandleAnalysis) -> tuple[str, float]:
    """
    Map CandleAnalysis.technical_bias to contract bias + strength.

    CandleAnalysis uses BiasDirection enum, not strings.
    We map to: bias string + bias_strength (0.0-1.0).
    """
    direction = analysis.technical_bias
    bias = _BIAS_MAP.get(direction, "neutral")
    # bias_strength from CandleAnalysis if available, else fallback
    strength = getattr(analysis, "bias_strength", None) or _STRENGTH_MAP.get(direction, 0.5)
    return bias, round(float(strength), 4)


def _build_analysis_window(bar_count: int) -> str:
    """Convert bar count to window string. For daily bars, use '1D' as convention."""
    if bar_count <= 1:
        return "1D"
    return f"{bar_count} bars"


def _assess_data_quality(analysis: CandleAnalysis) -> str:
    """Assess data quality from CandleAnalysis fields."""
    if not analysis.close or analysis.close <= 0:
        return "no_data"
    if getattr(analysis, "atr_14", 0) <= 0:
        return "degraded"
    if getattr(analysis, "rsi_14", None) is None:
        return "degraded"
    return "ok"


def _build_explanation_zh(analysis: CandleAnalysis, bias: str) -> str:
    """
    Build a one-line Chinese explanation from CandleAnalysis.
    Falls back to CandleAnalysis.bias_explanation_zh if available.
    """
    if analysis.bias_explanation_zh:
        return analysis.bias_explanation_zh

    # Fallback construction
    structure = analysis.structure_state.value
    rsi = getattr(analysis, "rsi_14", None)
    patterns = analysis.detected_patterns

    parts = []
    parts.append(f"結構:{structure}")

    if rsi is not None:
        rsi_str = f"RSI={rsi:.1f}"
        parts.append(rsi_str)

    if patterns:
        names = ",".join(p.name.value for p in patterns[:2])
        parts.append(f"型態:{names}")

    direction_word = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(bias, "")
    return f"[{direction_word}] " + " | ".join(parts)


# ── Main mapper ──────────────────────────────────────────────────────────────

def map_candle_to_engine_output(
    analysis: CandleAnalysis,
    run_id: Optional[str] = None,
) -> EngineOutput:
    """
    Convert a CandleAnalysis (M1 output) to a unified EngineOutput.

    Parameters
    ----------
    analysis : CandleAnalysis — M1 raw output
    run_id   : optional UUID string, auto-generated if not provided

    Returns
    -------
    EngineOutput — ready for history write + Fusion Engine
    """
    bias, bias_strength = _map_bias(analysis)
    data_quality = _assess_data_quality(analysis)
    explanation = _build_explanation_zh(analysis, bias)

    # analysis_window: bar count from analysis or default to 30 (standard)
    bar_count = getattr(analysis, "bar_count", 30)
    window_str = _build_analysis_window(bar_count)

    # Build source_payload: use CandleAnalysis.to_dict() as source of truth
    source_payload = analysis.to_dict() if hasattr(analysis, "to_dict") else {}

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    output = EngineOutput(
        engine_name     = "candlestick",
        run_id          = run_id or analysis.run_id,
        symbol          = "XAUUSD",
        timestamp       = now_utc,
        timeframe       = "1D",
        bias            = bias,
        bias_strength   = bias_strength,
        confidence      = None,          # V4+ will compute cross-engine confidence
        explanation_zh  = explanation,
        data_quality_flag = data_quality,
        analysis_window   = window_str,
        source_payload    = source_payload,
    )

    logger.info(
        "[CandleMapper] mapped CandleAnalysis → EngineOutput | "
        f"bias={bias} strength={bias_strength} quality={data_quality} "
        f"run_id={output.run_id_short}"
    )

    return output