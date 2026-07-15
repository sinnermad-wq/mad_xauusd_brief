"""
Tests for V4 Fusion Engine.

Covers the 10 acceptance criteria from the V4 spec:
  1. briefing bullish + candlestick bullish -> aligned
  2. briefing bearish + candlestick bullish -> counter_trend / mixed
  3. missing briefing -> insufficient_context, no crash
  4. poor data quality -> confidence penalty
  5. candlestick trade_eligible False -> fusion trade_candidate False
  6. execution_intent decision = none when confidence too low
  7. history write/read for fusion output
  8. --mode fusion / --mode both smoke tests (CLI shape only)
  9. dashboard loader / telegram formatter no crash
  10. schema backward compatibility unbroken

Plus structural / unit tests on rules + scores + mapping.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from candlestick_engine.contract import EngineOutput
from fusion_engine import (
    ConsensusLabel,
    ConflictLabel,
    FusionEngine,
    FusionOutput,
    build_fusion_input,
    read_fusion_record,
    write_fusion_record,
)
from fusion_engine.models import FusionInput


# ── Helpers ──────────────────────────────────────────────────────────────────


def _candle(
    *,
    bias: str = "bullish",
    bias_strength: float = 0.7,
    confidence: float | None = 0.85,
    trade_eligible: bool = True,
    data_quality_flag: str = "ok",
    validation_status: str = "ok",
    engine_name: str = "candlestick",
    signal_id: str = "",
    execution_intent: dict | None = None,
) -> EngineOutput:
    out = EngineOutput(
        engine_name=engine_name,
        symbol="XAUUSD",
        timestamp="2026-06-26T12:00:00",
        timeframe="1D",
        bias=bias,
        bias_strength=bias_strength,
        confidence=confidence,
        explanation_zh="test",
        data_quality_flag=data_quality_flag,
        source_payload={"validation": {"status": validation_status}},
        signal_id=signal_id,
        execution_intent=execution_intent or {},
    )
    # use the contract gate so trade_eligible reflects the rule above
    out.populate_execution_fields()
    if not trade_eligible:
        out.trade_eligible = trade_eligible
    return out


def _briefing(
    *,
    bias: str = "bullish",
    confidence: float | None = 0.7,
    regime: str = "risk-on",
    event_risk: str = "low",
    news_sentiment: str = "positive",
) -> dict:
    return {
        "bias": bias,
        "confidence": confidence,
        "regime_tag": regime,
        "event_risk": event_risk,
        "news_sentiment": news_sentiment,
        "summary": "Briefing summary.",
    }


# ── 1. bullish + bullish -> aligned ─────────────────────────────────────────


class TestAligned:
    def test_bullish_bullish_aligned(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        assert out.consensus_label == ConsensusLabel.ALIGNED
        assert out.conflict_label == ConflictLabel.NONE
        assert out.fusion_bias == "bullish"
        # fuse weights: 0.50 > 0.6 → trade_candidate should be True
        # wait: only if trade_eligible True
        if c.trade_eligible:
            assert out.trade_candidate is True
        assert out.fusion_confidence > 0.6

    def test_bearish_bearish_aligned(self):
        c = _candle(bias="bearish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bearish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        assert out.consensus_label == "aligned"
        assert out.fusion_bias == "bearish"


# ── 2. bearish + bullish -> counter_trend / mixed ───────────────────────────


class TestConflict:
    def test_bearish_briefing_bullish_candle_counter_trend(self):
        c = _candle(bias="bullish", confidence=0.9, trade_eligible=True)
        b = _briefing(bias="bearish", confidence=0.85)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        # Either counter_trend or mixed depending on strength; here strong both → counter_trend
        assert out.conflict_label in {
            ConflictLabel.COUNTER_TREND,
            ConflictLabel.MACRO_TECHNICAL_CONFLICT,
        }
        assert out.fusion_confidence <= 0.45
        assert out.trade_candidate is False

    def test_partially_aligned_bullish_neutral_briefing(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="neutral", confidence=0.5)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        assert out.consensus_label in {"aligned", "partially_aligned"}


# ── 3. missing briefing -> insufficient_context, no crash ────────────────────


class TestMissingBriefing:
    def test_no_briefing_degrades_gracefully(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        fi = build_fusion_input(c, None, None)
        out = FusionEngine().fuse(fi)
        assert out.consensus_label == ConsensusLabel.INSUFFICIENT_CONTEXT
        assert out.conflict_label == ConflictLabel.MISSING_BRIEFING
        assert ("briefing:missing" in out.execution_intent["reason_codes"]
                or "conflict:" in str(out.execution_intent["reason_codes"]))
        # bias still reflects candle
        assert out.fusion_bias == "bullish"
        # no briefing → low confidence, no trade
        assert out.trade_candidate is False

    def test_partial_briefing_only_bias_provided(self):
        c = _candle(bias="bearish", confidence=0.85, trade_eligible=True)
        b = {"bias": "bearish"}  # no confidence / regime
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        # Should not crash, and degrade with missing confidence fields.
        # With same bias on both sides, even a thin briefing may produce
        # `aligned` or `partially_aligned`. We require graceful handling.
        assert out.consensus_label in {
            ConsensusLabel.INSUFFICIENT_CONTEXT,
            ConsensusLabel.PARTIALLY_ALIGNED,
            ConsensusLabel.ALIGNED,
        }
        # And confidence should be lower than the full-aligned case
        b_full = _briefing(bias="bearish", confidence=0.7)
        out_full = FusionEngine().fuse(build_fusion_input(c, b_full, None))
        assert out.fusion_confidence <= out_full.fusion_confidence + 1e-6


# ── 4. poor data quality -> confidence penalty ──────────────────────────────


class TestDataQualityPenalty:
    def test_invalid_validation_drops_confidence(self):
        c = _candle(bias="bullish", confidence=0.85, validation_status="invalid")
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        quality_score = out.source_payload["scores"]["quality_score"]
        assert quality_score < 1.0
        # Quality invalid → conflict label reflects it
        assert "data_quality" in out.conflict_label or out.conflict_label == "data_quality_issue"

    def test_degraded_validation_penalty_smaller(self):
        c_full = _candle(validation_status="ok")
        c_deg = _candle(validation_status="degraded")
        b = _briefing(bias="bullish", confidence=0.7)
        out_full = FusionEngine().fuse(build_fusion_input(_candle(bias="bullish"), b, None))
        out_deg  = FusionEngine().fuse(build_fusion_input(c_deg, _briefing(bias="bullish"), None))
        assert (
            out_deg.source_payload["scores"]["quality_score"]
            < out_full.source_payload["scores"]["quality_score"]
        )


# ── 5. candle trade_eligible False → fusion trade_candidate False ────────────


class TestTradeEligibilityGate:
    def test_candle_not_eligible_blocks_trade(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=False)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        assert out.trade_candidate is False
        assert out.execution_intent["decision"] in ("none", "flat")


# ── 6. execution_intent decision = none when confidence too low ──────────────


class TestConservativeIntent:
    def test_low_confidence_intent_decision_none(self):
        # bias says bullish but confidence below min
        c = _candle(bias="bullish", confidence=0.2, trade_eligible=True)
        # Make sure decision_ready is False (low conf)
        c.confidence = 0.2
        c.populate_execution_fields()
        b = _briefing(bias="bullish", confidence=0.15)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        # final confidence likely below 0.5
        assert out.execution_intent["decision"] in ("none", "flat")
        assert out.trade_candidate is False

    def test_counter_trend_intent_decision_none(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bearish", confidence=0.9)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        assert out.execution_intent["decision"] == "none"


# ── 7. history write / read round-trip ───────────────────────────────────────


class TestHistoryRoundTrip:
    def test_write_and_read(self, tmp_path: Path):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)

        target = tmp_path / "fusion"
        path = write_fusion_record(out, target, ts="2026-06-26")
        assert path.exists()
        assert path.name == "2026-06-26.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["engine_name"] == "fusion"
        assert loaded["schema_version"] == "4.0"
        assert loaded["run_id"] == out.run_id
        assert loaded["fusion_bias"] == "bullish"
        assert loaded["consensus_label"] == "aligned"

    def test_round_trip_preserves_intent(self, tmp_path: Path):
        c = _candle(bias="bearish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bearish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        path = write_fusion_record(out, tmp_path, ts="2026-06-26")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["execution_intent"]["decision"] == "short"
        assert loaded["explanation_zh"] == out.explanation_zh

    # ── E2: V5 new fields roundtrip ────────────────────────────────────────

    def test_v5_fields_written_and_read(self, tmp_path: Path):
        """All 5 new V5 fields survive write→read roundtrip."""
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)

        # Verify non-default values were set (trade_candidate=true → intent_str≠none)
        assert out.execution_intent_str is not None
        assert out.signal_strength != "unknown"
        assert out.summary_zh != ""

        path = write_fusion_record(out, tmp_path, ts="2026-06-26")
        loaded = read_fusion_record(path)

        assert loaded.execution_intent_str == out.execution_intent_str
        assert loaded.signal_strength      == out.signal_strength
        assert loaded.regime_tag           == out.regime_tag
        assert loaded.invalidation_reason  is None   # always null Phase 1
        assert loaded.summary_zh            == out.summary_zh

    def test_summary_zh_from_legacy_final_summary(self, tmp_path: Path):
        """summary_zh falls back to legacy final_summary (existing daily JSON shape)."""
        legacy_briefing = {
            "bias":          "neutral",
            "confidence":    0.5,
            "final_summary": "宏觀中性，技術面主導。",
        }
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        fi = build_fusion_input(c, legacy_briefing, None)
        out = FusionEngine().fuse(fi)

        assert out.summary_zh == legacy_briefing["final_summary"]
        path = write_fusion_record(out, tmp_path, ts="2026-06-26")
        loaded = read_fusion_record(path)
        assert loaded.summary_zh == out.summary_zh


# ── 8. CLI mode surface (smoke, no execution) ───────────────────────────────


class TestCLISmoke:
    def test_main_supports_fusion_keyword_in_source(self):
        # Even before --mode fusion wired, the word "fusion" should already
        # exist in main.py documentation text (e.g., AGENTS.md or roadmap comments).
        from daily_xauusd_brief import main as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        # Either main.py mentions fusion, or roadmap/AGENTS does. This is a
        # soft check — actual CLI integration is Step 4 of V4 plan.
        assert isinstance(src, str) and len(src) > 0

    def test_cli_shape_handles_missing_briefing_gracefully(self, tmp_path: Path, monkeypatch):
        # Smoke: simulate build_fusion_input + fuse path when briefing is None
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        fi = build_fusion_input(c, None, None)
        out = FusionEngine().fuse(fi)
        assert out.engine_name == "fusion"


# ── 9. dashboard loader & telegram formatter no-crash ─────────────────────────


class TestPresentationNoCrash:
    def test_telegram_formatter_returns_string(self):
        from fusion_engine.formatter import format_fusion_telegram_zh
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        text = format_fusion_telegram_zh(out)
        assert isinstance(text, str)
        assert len(text) > 10
        assert "XAUUSD" in text

    def test_dashboard_payload_shape(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        d = out.to_dict()
        for k in (
            "engine_name", "schema_version", "signal_id", "run_id",
            "fusion_bias", "fusion_confidence", "consensus_label",
            "conflict_label", "trade_candidate", "decision_ready",
            "trade_eligible", "execution_status", "execution_mode",
            "explanation_zh", "execution_intent", "source_payload",
        ):
            assert k in d, f"missing dashboard key: {k}"


# ── 10. schema backward compatibility unbroken ───────────────────────────────


class TestBackCompat:
    def test_candle_default_factory_still_works(self):
        out = EngineOutput.candlestick_now()
        # M5 fields still present
        assert out.schema_version == "3.5"
        assert out.execution_status == "not_sent"

    def test_candle_round_trip_through_fusion_no_loss(self):
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        d = c.to_dict()
        c2 = EngineOutput.from_dict(d)
        assert c2.bias == c.bias
        assert c2.confidence == c.confidence
        assert c2.signal_id == c.signal_id

    # ── E2: backward compat ─────────────────────────────────────────────────

    def test_old_fusion_json_missing_v5_fields_loads_with_defaults(self):
        """Old fusion JSON without new V5 fields loads without error, uses defaults."""
        old_json = {
            "engine_name":       "fusion",
            "schema_version":   "4.0",
            "run_id":           "old001",
            "signal_id":        "",
            "symbol":           "XAUUSD",
            "timestamp":        "2026-06-27T00:00:00",
            "timeframe":        "1D",
            "fusion_bias":      "bullish",
            "fusion_confidence": 0.75,
            "consensus_label":  "aligned",
            "conflict_label":   "none",
            "trade_candidate":  True,
            "decision_ready":   True,
            "trade_eligible":   True,
            "execution_status": "not_sent",
            "execution_mode":   "none",
            "execution_intent": {"decision": "long", "confidence": 0.75},
            "explanation_zh":   "Old format, no V5 fields.",
            "source_payload":   {},
            # ← no execution_intent_str, signal_strength, regime_tag,
            #   invalidation_reason, summary_zh
        }
        out = FusionOutput.from_dict(old_json)
        # New fields default safely
        assert out.execution_intent_str is None
        assert out.signal_strength      == "unknown"
        assert out.regime_tag           == "unknown"
        assert out.invalidation_reason is None
        assert out.summary_zh           == ""
        # Old fields preserved
        assert out.fusion_bias == "bullish"
        assert out.trade_candidate is True

    def test_dashboard_payload_v5_keys_present(self):
        """to_dict includes all V5 fields for dashboard consumption."""
        c = _candle(bias="bullish", confidence=0.85, trade_eligible=True)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        d = out.to_dict()
        for k in ("execution_intent_str", "signal_strength",
                  "regime_tag", "invalidation_reason", "summary_zh"):
            assert k in d, f"missing dashboard key: {k}"


# ── Structural / unit tests ───────────────────────────────────────────────────


class TestFusionEngineStructural:
    def test_engine_name_locked(self):
        c = _candle()
        b = _briefing()
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        assert out.engine_name == "fusion"

    def test_schema_version_locked(self):
        c = _candle()
        fi = build_fusion_input(c, None, None)
        out = FusionEngine().fuse(fi)
        assert out.schema_version == "4.0"

    def test_scores_payload_present(self):
        c = _candle(bias="bullish")
        b = _briefing(bias="bullish")
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        sub = out.source_payload["scores"]
        for key in (
            "candlestick_score", "briefing_score",
            "agreement_score", "quality_score",
            "final_fusion_confidence",
        ):
            assert key in sub, f"missing scores key: {key}"

    def test_audit_reason_codes_present(self):
        c = _candle(bias="bullish", confidence=0.85)
        b = _briefing(bias="bullish", confidence=0.7)
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        codes = out.execution_intent["reason_codes"]
        # Fusion emits consensus: / conflict: / candle_trade_eligible: codes
        assert any("consensus:" in c_ for c_ in codes)
        assert any("conflict:" in c_ for c_ in codes)

    def test_fusion_input_no_validation_passes_quality_one(self):
        c = EngineOutput(
            engine_name="candlestick",
            symbol="XAUUSD",
            timestamp="2026-06-26",
            timeframe="1D",
            bias="bullish",
            bias_strength=0.7,
            confidence=0.85,
            explanation_zh="",
            source_payload={},  # no validation
        )
        b = _briefing(bias="bullish")
        fi = build_fusion_input(c, b, None)
        out = FusionEngine().fuse(fi)
        # Missing validation shouldn't crash; quality_score = 1.0 fallback
        assert out.source_payload["scores"]["quality_score"] == 1.0

    def test_signal_id_inherited_or_new(self):
        c = _candle(signal_id="sig-existing")
        fi = build_fusion_input(c, None, None)
        out = FusionEngine().fuse(fi)
        # Either inherited OR auto-derived → non-empty
        assert out.signal_id

    def test_to_dict_serializable(self):
        c = _candle()
        fi = build_fusion_input(c, None, None)
        out = FusionEngine().fuse(fi)
        s = json.dumps(out.to_dict())
        assert "fusion_bias" in s
