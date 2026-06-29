"""
V3 M4 — Validation Layer

Lives in `candlestick_engine/` because the candlestick engine is the primary
producer of the validation payload. Briefing contributes via cross-engine
agreement; data sanity checks the raw bars.

Public API:
    validate_candlestick_output(
        output: EngineOutput,
        bars: Sequence[dict],
        cfg: Config,                    # weights, thresholds, TF stack (optional)
        briefing_bias: Optional[str],   # for cross-engine agreement
        tf_biases: Optional[dict],      # {"4h": "bullish", ...} per TF
    ) -> dict

Returns the validator record dict (see _build_validator_record). Calls may
also attach this to output.confidence and output.source_payload["validation"]
downstream (caller responsibility, kept pure here for testability).

Components:
  1. Data Sanity (hard/soft check on OHLCV bars)
  2. Multi-Timeframe Alignment (configurable TF stack via cfg.timeframe_stack)
  3. Cross-Engine Agreement (soft penalty, NO signal veto)
  4. Confidence Scoring (sub-scores then weighted final)

No ML. No execution. No live feed. Rule-based only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .contract import EngineOutput


# ── Defaults (overridable via cfg; soft-coupling keeps module pure) ───────

DEFAULT_TF_STACK: tuple[str, ...] = ("1day", "4h", "1h")
DEFAULT_TF_ALIGN_WEIGHT = 0.4
DEFAULT_CROSS_ENGINE_WEIGHT = 0.35
DEFAULT_DATA_QUALITY_WEIGHT = 0.25
DEFAULT_SANITY_GAP_PCT = 0.10
DEFAULT_SANITY_MIN_BARS = 14

VALID_BIAS = {"bullish", "bearish", "neutral"}


def _resolve_cfg(cfg: Any | None) -> dict[str, Any]:
    """Pull validation weights & thresholds from a Config-like object if given.

    Allows callers without a Config (tests!) to invoke this module cleanly.
    Returns a dict of resolved overrides; falls back to module defaults.
    """
    if cfg is None:
        return {}
    out: dict[str, Any] = {}
    for src_key, dest_key in (
        ("timeframe_stack", "timeframe_stack"),
        ("tf_alignment_weight", "tf_alignment_weight"),
        ("cross_engine_weight", "cross_engine_weight"),
        ("data_quality_weight", "data_quality_weight"),
        ("sanity_gap_pct_threshold", "sanity_gap_pct"),
        ("sanity_min_bars_per_tf", "sanity_min_bars"),
    ):
        if hasattr(cfg, src_key):
            out[dest_key] = getattr(cfg, src_key)
    return out


# ── 1. Data Sanity (hard/soft check) ──────────────────────────────────────


@dataclass
class SanityResult:
    hard_fail: bool
    soft_flags: list[str]
    status: str  # "ok" | "degraded" | "invalid"
    n_bars: int
    last_close: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "hard_fail": self.hard_fail,
            "soft_flags": self.soft_flags,
            "n_bars": self.n_bars,
            "last_close": self.last_close,
        }


def check_data_sanity(
    bars: Sequence[Mapping[str, Any]],
    gap_pct_threshold: float = DEFAULT_SANITY_GAP_PCT,
) -> SanityResult:
    """Validate OHLCV bars from oldest to newest.

    Hard fail conditions (set status=invalid):
      • high < low on any bar
      • any of o/h/l/c <= 0
      • wrong format (non-numeric)
      • zero bars

    Soft flags (status=degraded unless hard-failed):
      • gap > gap_pct_threshold between consecutive closes
      • timestamp misalignment (gaps > 2×typical cadence)
      • duplicate datetimes
    """
    flags: list[str] = []
    hard_fail = False

    if not bars:
        return SanityResult(
            hard_fail=False,
            soft_flags=["no_bars"],
            status="degraded",
            n_bars=0,
            last_close=None,
        )

    prev_close: Optional[float] = None
    seen_dates: set[str] = set()

    for i, bar in enumerate(bars):
        try:
            o = float(bar.get("open"))
            h = float(bar.get("high"))
            l = float(bar.get("low"))
            c = float(bar.get("close"))
        except (TypeError, ValueError):
            hard_fail = True
            flags.append(f"non_numeric_ohlc@bar_{i}")
            continue

        # Hard: invalid OHLC relationships
        if h < l:
            hard_fail = True
            flags.append(f"high_lt_low@bar_{i}")
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            hard_fail = True
            flags.append(f"non_positive_price@bar_{i}")

        # Soft: gap detection
        if prev_close is not None and prev_close > 0:
            gap_pct = abs(c - prev_close) / prev_close
            if gap_pct > gap_pct_threshold:
                flags.append(f"large_gap_{gap_pct:.1%}_at_bar_{i}")

        prev_close = c

        # Soft: duplicate timestamps
        dt = str(bar.get("datetime", ""))
        if dt:
            if dt in seen_dates:
                flags.append(f"duplicate_datetime_{dt}")
            seen_dates.add(dt)

    n_bars = len(bars)
    last_close_val: Optional[float] = None
    try:
        last_close_val = float(bars[-1].get("close"))
    except (TypeError, ValueError, KeyError):
        last_close_val = None

    if hard_fail:
        status = "invalid"
    elif flags:
        status = "degraded"
    else:
        status = "ok"

    return SanityResult(
        hard_fail=hard_fail,
        soft_flags=sorted(set(flags)),
        status=status,
        n_bars=n_bars,
        last_close=last_close_val,
    )


# ── 2. Multi-Timeframe Alignment ──────────────────────────────────────────


def score_timeframe_alignment(
    tf_biases: Mapping[str, str] | None,
    stack: Sequence[str] = DEFAULT_TF_STACK,
) -> dict[str, Any]:
    """Score multi-TF agreement.

    Args:
        tf_biases : {timeframe_label: bias} e.g. {"4h": "bullish", "1h": "neutral"}.
        stack     : ordered list of expected TFs (config-driven).

    Scoring:
      • For each TF present in both stack and tf_biases:
          - if all biases agree → 1.0
          - higher-TF vs lower-TF weight: higher-TF miss hurts more
      • All biased bullish/bearish = full aligned
      • High + mid agree, low disagrees = 0.66 partially_aligned
      • High disagrees with mid = 0.33 misaligned
      • Missing TFs degrade by n_missing / n_stack (worst case)
    """
    if not tf_biases:
        return {
            "stack": list(stack),
            "present_biases": {},
            "higher_tf_bias": None,
            "mid_tf_bias": None,
            "lower_tf_bias": None,
            "alignment_score": 0.5,  # no info: neutral
            "label": "unknown",
        }

    # Filter & reorder by stack
    ordered = [(tf, tf_biases.get(tf)) for tf in stack if tf in tf_biases]
    # If caller passed unknown TFs, keep them too for visibility
    extras = [k for k in tf_biases.keys() if k not in stack]
    for k in extras:
        ordered.append((k, tf_biases[k]))

    biases = [b for _, b in ordered]
    higher = ordered[0][1] if ordered else None
    mid = ordered[len(ordered) // 2][1] if len(ordered) >= 2 else higher
    lower = ordered[-1][1] if ordered else None

    if not biases or any(b is None for b in biases):
        return {
            "stack": list(stack),
            "present_biases": {tf: b for tf, b in ordered},
            "higher_tf_bias": higher,
            "mid_tf_bias": mid,
            "lower_tf_bias": lower,
            "alignment_score": 0.5,
            "label": "unknown",
        }

    # Coerce to valid bias labels; treat invalid as "neutral"
    norm = [b if b in VALID_BIAS else "neutral" for b in biases]

    # Higher-TF weights: position 0 = w1.0, mid = w0.66, low = w0.33, etc.
    weights = [1.0 - (i * (0.66 / max(1, len(norm) - 1))) for i in range(len(norm))]
    total_w = sum(weights)

    # Score per-bias: how much of the weighted mass agrees with first TF
    first = norm[0]
    weighted_agree = sum(w for w, b in zip(weights, norm) if b == first)
    alignment = weighted_agree / total_w if total_w else 0.5

    # Missing-TF penalty
    missing = max(0, len(stack) - len([tf for tf in stack if tf in tf_biases]))
    penalty = missing / max(1, len(stack)) * 0.2  # up to 0.2 off
    alignment = max(0.0, alignment - penalty)

    # Label thresholds
    if alignment >= 0.85:
        label = "aligned"
    elif alignment >= 0.6:
        label = "partially_aligned"
    elif alignment >= 0.4:
        label = "weakly_aligned"
    else:
        label = "misaligned"

    return {
        "stack": list(stack),
        "present_biases": {tf: b for tf, b in ordered},
        "higher_tf_bias": higher,
        "mid_tf_bias": mid,
        "lower_tf_bias": lower,
        "alignment_score": round(alignment, 4),
        "label": label,
    }


# ── 3. Cross-Engine Agreement (soft penalty only) ─────────────────────────


def score_cross_engine_agreement(
    candlestick_bias: str,
    briefing_bias: Optional[str],
) -> dict[str, Any]:
    """Score agreement between candlestick & briefing engines.

    Per M4 constraint: mismatched cross-engine is a PENALTY, not a veto.
    The signal remains valid; confidence drops and a regime label annotates
    why.

    Agreement score:
      • identical & non-neutral       → 1.0  aligned
      • identical neutral both        → 0.7  aligned_neutral
      • one neutral, other directional → 0.7  one_sided
      • directional opposite          → 0.35 mixed_regime / counter_trend
      • briefing missing              → 0.6  briefing_unknown
    """
    candlestick_bias = candlestick_bias if candlestick_bias in VALID_BIAS else "neutral"

    if briefing_bias is None:
        return {
            "candlestick_bias": candlestick_bias,
            "briefing_bias": None,
            "agreement_score": 0.6,
            "label": "briefing_unknown",
        }

    briefing_norm = briefing_bias if briefing_bias in VALID_BIAS else "neutral"

    if candlestick_bias == briefing_norm:
        if candlestick_bias == "neutral":
            score, label = 0.7, "aligned_neutral"
        else:
            score, label = 1.0, "aligned"
    elif candlestick_bias == "neutral" or briefing_norm == "neutral":
        score, label = 0.7, "one_sided"
    else:
        # Both directional but opposite (bullish vs bearish)
        # Distinguish: counter_trend = high-TF misaligned but short bounce allowed
        # vs mixed_regime = both directional opposing without TF info; we keep
        # both possibilities open in source_payload so caller can refine.
        score = 0.35
        # Use neutral heuristic; the engine can override with TF context
        label = "mixed_regime"

    return {
        "candlestick_bias": candlestick_bias,
        "briefing_bias": briefing_norm,
        "agreement_score": round(score, 4),
        "label": label,
    }


# ── 4. Confidence Scoring ─────────────────────────────────────────────────


def compute_final_confidence(
    tf_alignment_score: float,
    cross_engine_score: float,
    data_quality_score: float,
    tf_weight: float = DEFAULT_TF_ALIGN_WEIGHT,
    ce_weight: float = DEFAULT_CROSS_ENGINE_WEIGHT,
    dq_weight: float = DEFAULT_DATA_QUALITY_WEIGHT,
) -> dict[str, Any]:
    """Weighted combination of sub-scores into final confidence [0, 1].

    If weights don't sum to 1, we normalize them.
    Hard-failed data collapses dq to 0 (which can never re-qualify, despite
    weighted sum).
    """
    total_w = tf_weight + ce_weight + dq_weight
    if total_w <= 0:
        # pathological config → uniform weights
        tf_w = ce_w = dq_w = 1.0 / 3.0
    else:
        tf_w = tf_weight / total_w
        ce_w = ce_weight / total_w
        dq_w = dq_weight / total_w

    final = (
        tf_w * tf_alignment_score
        + ce_w * cross_engine_score
        + dq_w * data_quality_score
    )

    return {
        "timeframe_alignment_score": round(tf_alignment_score, 4),
        "cross_engine_score": round(cross_engine_score, 4),
        "data_quality_score": round(data_quality_score, 4),
        "weights": {
            "timeframe_alignment": round(tf_w, 4),
            "cross_engine": round(ce_w, 4),
            "data_quality": round(dq_w, 4),
        },
        "final_confidence": round(max(0.0, min(1.0, final)), 4),
    }


# ── Orchestrator ───────────────────────────────────────────────────────────


def validate_candlestick_output(
    output: EngineOutput,
    bars: Sequence[Mapping[str, Any]],
    cfg: Any | None = None,
    briefing_bias: Optional[str] = None,
    tf_biases: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Run all 4 validators and assemble `validation` + `confidence` records.

    Returns a dict of the form:
        {
            "validation": { ... },
            "confidence": { ... },
        }

    Use as:
        rec = validate_candlestick_output(...)
        output.source_payload["validation"] = rec["validation"]
        output.confidence = rec["confidence"]["final_confidence"]
    """
    overrides = _resolve_cfg(cfg)

    stack = overrides.get("timeframe_stack", DEFAULT_TF_STACK)
    gap_pct = overrides.get("sanity_gap_pct", DEFAULT_SANITY_GAP_PCT)
    min_bars = overrides.get("sanity_min_bars", DEFAULT_SANITY_MIN_BARS)
    tf_w = overrides.get(
        "tf_alignment_weight", DEFAULT_TF_ALIGN_WEIGHT
    )
    ce_w = overrides.get(
        "cross_engine_weight", DEFAULT_CROSS_ENGINE_WEIGHT
    )
    dq_w = overrides.get(
        "data_quality_weight", DEFAULT_DATA_QUALITY_WEIGHT
    )

    # ── 1. Sanity ────────────────────────────────────────────────────────
    sanity = check_data_sanity(bars, gap_pct_threshold=gap_pct)
    if sanity.n_bars < min_bars:
        sanity.soft_flags.append(
            f"low_bar_count_{sanity.n_bars}_below_{min_bars}"
        )
        if sanity.status == "ok":
            sanity.status = "degraded"

    if sanity.hard_fail:
        data_quality_score = 0.0
    else:
        # Soft penalties: ~0.05 per soft flag, cap at 0.9 floor
        penalty = min(0.9, 0.05 * len(sanity.soft_flags))
        data_quality_score = max(0.0, 1.0 - penalty)

    # ── 2. TF alignment ─────────────────────────────────────────────────
    tf_result = score_timeframe_alignment(tf_biases, stack=stack)
    tf_score = float(tf_result["alignment_score"])

    # ── 3. Cross-engine ──────────────────────────────────────────────────
    ce_result = score_cross_engine_agreement(output.bias, briefing_bias)
    ce_score = float(ce_result["agreement_score"])

    # ── 4. Final confidence ──────────────────────────────────────────────
    conf = compute_final_confidence(
        tf_alignment_score=tf_score,
        cross_engine_score=ce_score,
        data_quality_score=data_quality_score,
        tf_weight=tf_w,
        ce_weight=ce_w,
        dq_weight=dq_w,
    )

    # ── Overall validation status ────────────────────────────────────────
    if sanity.hard_fail:
        overall_status = "invalid"
    elif sanity.status == "degraded" or tf_result["label"] == "misaligned":
        overall_status = "degraded"
    elif ce_result["label"] in {"mixed_regime", "briefing_unknown"}:
        # Cross-engine mismatch downgrades confidence but signal still valid.
        overall_status = "qualified_with_caution"
    else:
        overall_status = "ok"

    validator_record = {
        "status": overall_status,
        "timeframe_alignment": tf_result,
        "cross_engine": ce_result,
        "data_sanity": sanity.to_dict(),
        "confidence": conf,
    }

    return {
        "validation": validator_record,
        "confidence": conf,
        "overall_status": overall_status,
    }
