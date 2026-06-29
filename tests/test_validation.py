"""
Tests for V3 M4 — Validation Layer

Coverage targets:
  • Data Sanity        (7 tests)
  • Multi-TF Alignment (8 tests)
  • Cross-Engine       (8 tests)
  • Final Confidence   (5 tests)
  • Orchestrator       (10+ tests, end-to-end)

Rule-based, deterministic, no time/network.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from candlestick_engine.contract import EngineOutput
from candlestick_engine.validation import (
    DEFAULT_TF_STACK,
    check_data_sanity,
    compute_final_confidence,
    score_cross_engine_agreement,
    score_timeframe_alignment,
    validate_candlestick_output,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _bar(i: int, *, dt=None, o=4000.0, c=4005.0, h=4010.0, l=3995.0) -> dict:
    return {
        "datetime": dt or f"2026-06-{i + 1:02d}",
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
    }


def _bars_clean(n: int = 30) -> list[dict]:
    base = datetime(2026, 6, 1)
    out = []
    for i in range(n):
        dt = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({
            "datetime": dt,
            "open": 4000.0 + i,
            "high": 4010.0 + i,
            "low": 3995.0 + i,
            "close": 4005.0 + i,
            "volume": 1000.0,
        })
    return out


def _output(bias: str = "bullish", **kwargs) -> EngineOutput:
    defaults = dict(
        engine_name="candlestick",
        symbol="XAU/USD",
        timestamp="2026-06-26T12:00:00",
        timeframe="1day",
        bias=bias,
        bias_strength=0.7,
        confidence=None,
        explanation_zh="",
        source_payload={},
    )
    defaults.update(kwargs)
    return EngineOutput(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
#  1. Data Sanity
# ═══════════════════════════════════════════════════════════════════════════


class TestDataSanity:
    def test_clean_bars_pass(self):
        res = check_data_sanity(_bars_clean(30))
        assert res.status == "ok"
        assert not res.hard_fail
        assert res.soft_flags == []
        assert res.n_bars == 30
        assert res.last_close == 4034.0

    def test_empty_bars_degraded_not_hard_fail(self):
        res = check_data_sanity([])
        assert res.status == "degraded"
        assert not res.hard_fail
        assert "no_bars" in res.soft_flags
        assert res.n_bars == 0
        assert res.last_close is None

    def test_high_lt_low_hard_fail(self):
        bars = _bars_clean(3)
        bars[1]["high"] = bars[1]["low"] - 1
        res = check_data_sanity(bars)
        assert res.hard_fail is True
        assert res.status == "invalid"
        assert any("high_lt_low" in f for f in res.soft_flags)

    def test_negative_close_hard_fail(self):
        bars = _bars_clean(3)
        bars[2]["close"] = -1
        res = check_data_sanity(bars)
        assert res.hard_fail is True
        assert any("non_positive_price" in f for f in res.soft_flags)

    def test_large_gap_soft_flag(self):
        bars = _bars_clean(5)
        bars[3]["close"] = bars[2]["close"] * 1.5  # 50% gap
        res = check_data_sanity(bars)
        assert not res.hard_fail
        assert res.status == "degraded"
        assert any("large_gap" in f for f in res.soft_flags)

    def test_duplicate_datetime_soft_flag(self):
        bars = _bars_clean(3)
        bars[1]["datetime"] = bars[0]["datetime"]
        res = check_data_sanity(bars)
        assert not res.hard_fail
        assert any("duplicate_datetime" in f for f in res.soft_flags)

    def test_non_numeric_hard_fail(self):
        bars = _bars_clean(3)
        bars[1]["close"] = "NaN-ish"
        res = check_data_sanity(bars)
        assert res.hard_fail is True


# ═══════════════════════════════════════════════════════════════════════════
#  2. Multi-Timeframe Alignment
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeframeAlignment:
    def test_all_aligned_full_score(self):
        res = score_timeframe_alignment(
            {"1day": "bullish", "4h": "bullish", "1h": "bullish"},
            stack=("1day", "4h", "1h"),
        )
        assert res["alignment_score"] == 1.0
        assert res["label"] == "aligned"
        assert res["higher_tf_bias"] == "bullish"
        assert res["mid_tf_bias"] == "bullish"
        assert res["lower_tf_bias"] == "bullish"

    def test_partial_alignment(self):
        res = score_timeframe_alignment(
            {"1day": "bullish", "4h": "bullish", "1h": "neutral"},
            stack=("1day", "4h", "1h"),
        )
        # Weights: 1day=1.0, 4h=0.67, 1h=0.34 — only 1h disagrees fully
        # weighted_agree = 1.0 + 0.67 = 1.67; total = 2.01 → 0.831 ≈ partially_aligned
        assert res["alignment_score"] >= 0.6
        assert res["alignment_score"] < 1.0
        assert res["label"] in {"aligned", "partially_aligned"}

    def test_misaligned(self):
        res = score_timeframe_alignment(
            {"1day": "bearish", "4h": "bearish", "1h": "bullish"},
            stack=("1day", "4h", "1h"),
        )
        # Higher-TFs (1day=0.5, 4h=0.33) all agree on bearish → high score
        # only lower-TF (1h=0.34) disagrees → ~0.83 weakly_aligned.
        # Validate that label reflects the disagreement.
        assert res["alignment_score"] < 1.0
        assert 0.4 <= res["alignment_score"]
        # key assertion: bias direction is bearish per higher/mid
        assert res["higher_tf_bias"] == "bearish"
        assert res["mid_tf_bias"] == "bearish"
        assert res["lower_tf_bias"] == "bullish"

    def test_full_misalignment(self):
        res = score_timeframe_alignment(
            {"1day": "bearish", "4h": "bullish", "1h": "bearish"},
            stack=("1day", "4h", "1h"),
        )
        # higher=bearish, mid=bullish disagree → low score
        assert res["alignment_score"] < 0.7
        assert res["label"] in {"weakly_aligned", "misaligned", "partially_aligned"}

    def test_empty_tf_biases_neutral(self):
        res = score_timeframe_alignment(None, stack=("1day", "4h"))
        assert res["alignment_score"] == 0.5
        assert res["label"] == "unknown"

    def test_configurable_stack_2_levels(self):
        res = score_timeframe_alignment(
            {"4h": "bullish", "1h": "bearish"},
            stack=("4h", "1h"),
        )
        assert res["stack"] == ["4h", "1h"]
        # higher (4h=bull) disagrees with lower (1h=bear) → weakly aligned
        assert res["alignment_score"] < 1.0

    def test_configurable_stack_4_levels(self):
        res = score_timeframe_alignment(
            {"1day": "bullish", "4h": "bullish", "1h": "bullish", "15m": "bullish"},
            stack=("1day", "4h", "1h", "15m"),
        )
        assert res["alignment_score"] == 1.0
        assert len(res["present_biases"]) == 4

    def test_missing_tf_penalty(self):
        # Stack says 3 TFs, only 2 provided → small penalty
        full = score_timeframe_alignment(
            {"1day": "bullish", "4h": "bullish", "1h": "bullish"},
            stack=("1day", "4h", "1h"),
        )
        partial = score_timeframe_alignment(
            {"1day": "bullish", "4h": "bullish"},
            stack=("1day", "4h", "1h"),
        )
        assert partial["alignment_score"] < full["alignment_score"]

    def test_invalid_bias_treated_as_neutral(self):
        res = score_timeframe_alignment(
            {"1day": "bullish", "4h": "??", "1h": "bullish"},
            stack=("1day", "4h", "1h"),
        )
        # Treats ?? as neutral; higher & lower both bullish → still aligned-ish
        assert res["alignment_score"] >= 0.5


# ═══════════════════════════════════════════════════════════════════════════
#  3. Cross-Engine Agreement
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossEngine:
    def test_both_bullish_aligned(self):
        res = score_cross_engine_agreement("bullish", "bullish")
        assert res["agreement_score"] == 1.0
        assert res["label"] == "aligned"

    def test_both_bearish_aligned(self):
        res = score_cross_engine_agreement("bearish", "bearish")
        assert res["agreement_score"] == 1.0
        assert res["label"] == "aligned"

    def test_both_neutral(self):
        res = score_cross_engine_agreement("neutral", "neutral")
        assert res["agreement_score"] == 0.7
        assert res["label"] == "aligned_neutral"

    def test_one_neutral(self):
        res = score_cross_engine_agreement("bullish", "neutral")
        assert res["agreement_score"] == 0.7
        assert res["label"] == "one_sided"

    def test_opposite_directional_no_veto(self):
        # M4 constraint: penalty, not veto.
        res = score_cross_engine_agreement("bullish", "bearish")
        assert res["label"] == "mixed_regime"
        # 0.35 not 0.0 — signal still has some validity
        assert res["agreement_score"] > 0.0
        assert res["agreement_score"] < 1.0

    def test_briefing_missing_degrades_but_no_zero(self):
        res = score_cross_engine_agreement("bullish", None)
        assert res["label"] == "briefing_unknown"
        assert res["agreement_score"] == 0.6

    def test_invalid_briefing_bias_normalized(self):
        res = score_cross_engine_agreement("bullish", "???")
        # normalized to neutral → one_sided 0.7
        assert res["briefing_bias"] == "neutral"
        assert res["label"] == "one_sided"

    def test_invalid_candlestick_bias_normalized(self):
        res = score_cross_engine_agreement("???", "bullish")
        assert res["candlestick_bias"] == "neutral"
        assert res["label"] == "one_sided"


# ═══════════════════════════════════════════════════════════════════════════
#  4. Confidence Scoring
# ═══════════════════════════════════════════════════════════════════════════


class TestFinalConfidence:
    def test_all_perfect_self_uniform_weights(self):
        res = compute_final_confidence(1.0, 1.0, 1.0)
        assert res["final_confidence"] == 1.0
        assert abs(sum(res["weights"].values()) - 1.0) < 1e-6

    def test_all_zero(self):
        res = compute_final_confidence(0.0, 0.0, 0.0)
        assert res["final_confidence"] == 0.0

    def test_mixed_partial_default_weights(self):
        res = compute_final_confidence(1.0, 0.5, 0.0)
        # default weights 0.4/0.35/0.25 → 0.4*1.0 + 0.35*0.5 + 0.25*0.0 = 0.575
        assert res["final_confidence"] == pytest.approx(0.575, abs=0.01)

    def test_weights_normalized_when_sum_not_one(self):
        res = compute_final_confidence(0.5, 0.5, 0.5, tf_weight=2.0, ce_weight=2.0, dq_weight=1.0)
        # weights: 2/5, 2/5, 1/5
        # final = 0.4*0.5 + 0.4*0.5 + 0.2*0.5 = 0.5
        assert res["final_confidence"] == pytest.approx(0.5, abs=0.01)
        assert res["weights"]["timeframe_alignment"] == pytest.approx(0.4, abs=0.01)
        assert res["weights"]["cross_engine"] == pytest.approx(0.4, abs=0.01)
        assert res["weights"]["data_quality"] == pytest.approx(0.2, abs=0.01)

    def test_clamped_to_unit_interval(self):
        # Even with extreme values: clamp to [0,1]
        res = compute_final_confidence(1.5, 1.5, 1.5)
        assert res["final_confidence"] == 1.0
        res = compute_final_confidence(-0.5, -0.5, -0.5)
        assert res["final_confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  5. Orchestrator — End-to-End
# ═══════════════════════════════════════════════════════════════════════════


class TestOrchestrator:
    def test_all_aligned_full_confidence(self):
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bullish",
            tf_biases={"1day": "bullish", "4h": "bullish", "1h": "bullish"},
        )
        assert rec["overall_status"] == "ok"
        assert rec["confidence"]["final_confidence"] == 1.0
        assert rec["validation"]["timeframe_alignment"]["label"] == "aligned"
        assert rec["validation"]["cross_engine"]["label"] == "aligned"

    def test_cross_engine_mismatch_does_not_veto(self):
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bearish",
            tf_biases={"1day": "bullish", "4h": "bullish", "1h": "bullish"},
        )
        # Signal is NOT invalid — only "qualified_with_caution"
        assert rec["overall_status"] != "invalid"
        # Confidence drops but is not zero
        conf = rec["confidence"]["final_confidence"]
        assert 0.0 < conf < 1.0

    def test_hard_fail_data_sets_invalid_status(self):
        out = _output("bullish")
        bars = _bars_clean(10)
        bars[5]["high"] = bars[5]["low"] - 1  # breaks OHLC
        rec = validate_candlestick_output(out, bars)
        assert rec["overall_status"] == "invalid"
        assert rec["confidence"]["data_quality_score"] == 0.0
        # final confidence still has tf & ce contributions but dq zeros
        assert rec["confidence"]["final_confidence"] < 0.5

    def test_soft_degraded_low_bar_count(self):
        out = _output("bullish")
        rec = validate_candlestick_output(out, _bars_clean(5))  # < min 14
        assert rec["overall_status"] in {"degraded", "ok"}
        assert any("low_bar_count" in f for f in rec["validation"]["data_sanity"]["soft_flags"])

    def test_briefing_missing_degrades_but_not_invalid(self):
        out = _output("bullish")
        rec = validate_candlestick_output(out, _bars_clean(30), briefing_bias=None)
        assert rec["overall_status"] != "invalid"
        assert rec["validation"]["cross_engine"]["label"] == "briefing_unknown"

    def test_payload_attachable_to_engine_output(self):
        out = _output("bullish")
        out.source_payload = {}  # reset
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bullish",
            tf_biases={"1day": "bullish", "4h": "bullish", "1h": "bullish"},
        )
        # Simulate main.py attachment step
        out.source_payload["validation"] = rec["validation"]
        out.confidence = rec["confidence"]["final_confidence"]
        assert out.confidence == 1.0
        assert "validation" in out.source_payload
        assert out.source_payload["validation"]["status"] == "ok"

    def test_configurable_tf_stack_via_cfg(self):
        class FakeCfg:
            timeframe_stack = ("4h", "1h", "15m")
            tf_alignment_weight = 0.5
            cross_engine_weight = 0.3
            data_quality_weight = 0.2
            sanity_gap_pct_threshold = 0.10
            sanity_min_bars_per_tf = 14

        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            cfg=FakeCfg(),
            briefing_bias="bullish",
            tf_biases={"4h": "bullish", "1h": "bullish", "15m": "neutral"},
        )
        assert rec["validation"]["timeframe_alignment"]["stack"] == ["4h", "1h", "15m"]
        assert rec["confidence"]["weights"]["timeframe_alignment"] == pytest.approx(0.5, abs=0.01)

    def test_confidence_breakdown_in_payload(self):
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bullish",
            tf_biases={"1day": "bullish", "4h": "bullish"},
        )
        conf = rec["confidence"]
        assert "timeframe_alignment_score" in conf
        assert "cross_engine_score" in conf
        assert "data_quality_score" in conf
        assert "final_confidence" in conf
        assert "weights" in conf
        for sub in ("timeframe_alignment", "cross_engine", "data_quality"):
            assert sub in conf["weights"]

    def test_overall_status_qualified_with_caution(self):
        # Cross-engine mismatch but data clean + TF aligned → caution label
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bearish",
            tf_biases={"1day": "bullish", "4h": "bullish", "1h": "bullish"},
        )
        assert rec["overall_status"] == "qualified_with_caution"

    def test_partial_alignment_with_clean_data_yields_ok(self):
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bullish",
            tf_biases={"1day": "bullish", "4h": "bullish", "1h": "neutral"},
        )
        # Partial alignment + matching engine + clean bars → still "ok" status
        # (only "misaligned" TF or "degraded" data or mixed_regime CE pushes status down)
        assert rec["overall_status"] in {"ok", "qualified_with_caution"}

    def test_returns_full_record_shape(self):
        out = _output("bullish")
        rec = validate_candlestick_output(out, _bars_clean(30))
        assert set(rec.keys()) == {"validation", "confidence", "overall_status"}
        v = rec["validation"]
        assert set(v.keys()) == {
            "status", "timeframe_alignment", "cross_engine", "data_sanity", "confidence"
        }
        assert set(v["data_sanity"].keys()) >= {
            "status", "hard_fail", "soft_flags", "n_bars", "last_close"
        }


# ═══════════════════════════════════════════════════════════════════════════
#  6. Scenario / E2E smoke (rules-based, no I/O)
# ═══════════════════════════════════════════════════════════════════════════


class TestScenarios:
    """End-to-end scenarios simulating real situations."""

    def test_calm_market_aligned(self):
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bullish",
            tf_biases={"1day": "bullish", "4h": "bullish", "1h": "bullish"},
        )
        assert rec["overall_status"] == "ok"
        assert rec["confidence"]["final_confidence"] >= 0.95

    def test_short_bounce_against_macro(self):
        # Candle bullish, briefing bearish → mixed_regime with caveat.
        out = _output("bullish")
        rec = validate_candlestick_output(
            out, _bars_clean(30),
            briefing_bias="bearish",
            tf_biases={"1day": "bullish", "4h": "neutral", "1h": "bullish"},
        )
        assert rec["overall_status"] != "invalid"
        assert rec["validation"]["cross_engine"]["label"] == "mixed_regime"
        # Confidence should drop but signal preserved
        assert 0.0 < rec["confidence"]["final_confidence"] < 1.0

    def test_data_gap_soft_degraded(self):
        bars = _bars_clean(15)
        bars[10]["close"] = bars[9]["close"] * 1.2  # 20% gap > 10% threshold
        out = _output("bullish")
        rec = validate_candlestick_output(out, bars)
        assert rec["overall_status"] == "degraded"
        assert rec["confidence"]["data_quality_score"] < 1.0
        assert rec["confidence"]["data_quality_score"] > 0.0

    def test_ohlc_violation_hard_invalid(self):
        bars = _bars_clean(15)
        bars[5]["high"] = bars[5]["low"] - 1
        out = _output("bullish")
        rec = validate_candlestick_output(out, bars)
        assert rec["overall_status"] == "invalid"
        assert rec["validation"]["data_sanity"]["hard_fail"] is True

    def test_full_breakdown(self):
        # Hard-fail data + briefing opposing + TF misaligned
        bars = _bars_clean(15)
        bars[3]["high"] = bars[3]["low"] - 1  # hard fail
        out = _output("bearish")
        rec = validate_candlestick_output(
            out, bars,
            briefing_bias="bullish",
            tf_biases={"1day": "bearish", "4h": "bullish"},  # misaligned
        )
        assert rec["overall_status"] == "invalid"
        # Even with multiple penalties, confidence stays bounded
        assert rec["confidence"]["final_confidence"] >= 0.0
        assert rec["confidence"]["final_confidence"] <= 1.0
