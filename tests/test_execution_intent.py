"""
Tests for V3 M5 — Execution-ready schema.

Coverage:
  • EngineOutput M5 default fields (no execution yet)
  • Backward-compatible `from_dict` (old payload without M5 still loads)
  • Forward-compatible `to_dict` (all 7 new fields serialised)
  • `build_execution_intent()` mapping (bullish→long, bearish→short, neutral→flat)
  • `populate_execution_fields()` rules:
      - decision_ready gating (confidence, data_quality, bias direction)
      - trade_eligible requires decision_ready AND validation.status != invalid
      - degraded data allowed when allow_degraded_data=True
      - signal_id derivation from run_id
      - execution_status preserved as "not_sent" (no service has acted)
      - execution_mode preserved as "none"
  • Risk-reward tuple → stop_loss/take_profit fields in intent
  • Reason codes forwarded into intent
  • JSON-serializability (no NoneType leakage in mandatory keys)
"""

from __future__ import annotations

import json

import pytest

from candlestick_engine.contract import EngineOutput


# ── Helpers ────────────────────────────────────────────────────────────────


def _output(
    *,
    bias: str = "bullish",
    confidence: float | None = 0.85,
    data_quality_flag: str = "ok",
    validation_status: str = "ok",
    engine_name: str = "candlestick",
    execution_status: str = "not_sent",
    execution_mode: str = "none",
    signal_id: str = "",
) -> EngineOutput:
    out = EngineOutput(
        engine_name=engine_name,
        symbol="XAUUSD",
        timestamp="2026-06-26T12:00:00",
        timeframe="1D",
        bias=bias,
        bias_strength=0.7,
        confidence=confidence,
        explanation_zh="",
        data_quality_flag=data_quality_flag,
        source_payload={"validation": {"status": validation_status}},
        signal_id=signal_id,
        execution_status=execution_status,
        execution_mode=execution_mode,
    )
    return out


# ── M5 default fields ──────────────────────────────────────────────────────


class TestM5Defaults:
    def test_default_execution_fields_are_safe(self):
        out = EngineOutput(
            engine_name="candlestick",
            symbol="XAUUSD",
            timestamp="2026-06-26T12:00:00",
            timeframe="1D",
            bias="neutral",
            bias_strength=0.0,
            confidence=None,
            explanation_zh="",
        )
        assert out.schema_version == "3.5"
        assert out.signal_id == ""
        assert out.decision_ready is False
        assert out.trade_eligible is False
        assert out.execution_status == "not_sent"
        assert out.execution_mode == "none"
        assert out.execution_intent == {}

    def test_to_dict_includes_all_m5_keys(self):
        out = _output()
        d = out.to_dict()
        # Required keys
        for k in (
            "schema_version",
            "signal_id",
            "decision_ready",
            "trade_eligible",
            "execution_status",
            "execution_mode",
            "execution_intent",
        ):
            assert k in d, f"missing key: {k}"

    def test_json_serializable(self):
        out = _output()
        out.populate_execution_fields()
        s = json.dumps(out.to_dict())  # must not raise
        assert "schema_version" in s


# ── Backward compatibility ─────────────────────────────────────────────────


class TestBackwardCompatibility:
    """Old payloads (M2/M3/M4 era) must still load."""

    def test_payload_without_m5_fields_loads_with_safe_defaults(self):
        old_payload = {
            "engine_name": "candlestick",
            "run_id": "abc123",
            "symbol": "XAUUSD",
            "timestamp": "2026-06-26T12:00:00",
            "timeframe": "1D",
            "bias": "bullish",
            "bias_strength": 0.6,
            "confidence": 0.7,
            "explanation_zh": "test",
            "data_quality_flag": "ok",
            "analysis_window": "1D",
            "source_payload": {},
            # NO: schema_version, signal_id, decision_ready, ...
        }
        out = EngineOutput.from_dict(old_payload)
        assert out.schema_version == "3.5"
        assert out.signal_id == ""
        assert out.decision_ready is False
        assert out.trade_eligible is False
        assert out.execution_status == "not_sent"
        assert out.execution_mode == "none"
        assert out.execution_intent == {}

    def test_roundtrip_preserves_m5_fields(self):
        out = _output()
        out.populate_execution_fields()
        d = out.to_dict()
        out2 = EngineOutput.from_dict(d)
        assert out2.schema_version == out.schema_version
        assert out2.signal_id == out.signal_id
        assert out2.decision_ready == out.decision_ready
        assert out2.trade_eligible == out.trade_eligible
        assert out2.execution_intent == out.execution_intent


# ── build_execution_intent ────────────────────────────────────────────────


class TestBuildExecutionIntent:
    def test_bullish_to_long(self):
        out = _output(bias="bullish", confidence=0.85)
        intent = out.build_execution_intent()
        assert intent["decision"] == "long"
        assert intent["strategy_id"] == "candlestick_v3"
        assert intent["symbol"] == "XAUUSD"
        assert intent["timeframe"] == "1D"
        assert intent["confidence"] == 0.85

    def test_bearish_to_short(self):
        out = _output(bias="bearish")
        intent = out.build_execution_intent()
        assert intent["decision"] == "short"

    def test_neutral_to_flat(self):
        out = _output(bias="neutral")
        intent = out.build_execution_intent()
        assert intent["decision"] == "flat"

    def test_engine_fusion_uses_fusion_strategy_id(self):
        out = _output(bias="bullish", engine_name="fusion")
        intent = out.build_execution_intent()
        assert intent["strategy_id"] == "fusion_v1"

    def test_confidence_none_defaults_to_zero_in_intent(self):
        out = _output(confidence=None)
        intent = out.build_execution_intent()
        assert intent["confidence"] == 0.0

    def test_risk_reward_populates_sl_tp(self):
        out = _output(bias="bullish")
        intent = out.build_execution_intent(risk_reward=(4020.0, 4080.0))
        assert intent["stop_loss"] == 4020.0
        assert intent["take_profit"] == 4080.0

    def test_no_risk_reward_leaves_sl_tp_none(self):
        out = _output(bias="bullish")
        intent = out.build_execution_intent()
        assert intent["stop_loss"] is None
        assert intent["take_profit"] is None

    def test_reason_codes_forwarded(self):
        out = _output(bias="bullish")
        intent = out.build_execution_intent(reason_codes=["A", "B"])
        assert intent["reason_codes"] == ["A", "B"]

    def test_no_reason_codes_empty_list(self):
        out = _output(bias="bullish")
        intent = out.build_execution_intent()
        assert intent["reason_codes"] == []

    def test_custom_strategy_id_honored(self):
        out = _output()
        intent = out.build_execution_intent(strategy_id="custom_v2")
        assert intent["strategy_id"] == "custom_v2"

    def test_unknown_bias_to_none(self):
        out = _output(bias="???")
        intent = out.build_execution_intent()
        assert intent["decision"] == "none"


# ── populate_execution_fields rules ───────────────────────────────────────


class TestPopulateRules:
    def test_strong_signal_passes(self):
        out = _output(bias="bullish", confidence=0.85, data_quality_flag="ok",
                       validation_status="ok")
        out.populate_execution_fields()
        assert out.decision_ready is True
        assert out.trade_eligible is True

    def test_default_confidence_min(self):
        # Default min is 0.6, so 0.6 should pass
        out = _output(confidence=0.6, validation_status="ok")
        out.populate_execution_fields()
        assert out.decision_ready is True

    def test_confidence_below_threshold_blocks(self):
        out = _output(confidence=0.5, validation_status="ok")
        out.populate_execution_fields()
        assert out.decision_ready is False
        assert out.trade_eligible is False

    def test_no_confidence_blocks(self):
        out = _output(confidence=None, validation_status="ok")
        out.populate_execution_fields()
        assert out.decision_ready is False

    def test_degraded_data_blocks_by_default(self):
        out = _output(data_quality_flag="degraded", confidence=0.85)
        out.populate_execution_fields()
        assert out.decision_ready is False

    def test_degraded_data_allowed_when_flag_set(self):
        out = _output(data_quality_flag="degraded", confidence=0.85,
                       validation_status="ok")
        out.populate_execution_fields(allow_degraded_data=True)
        assert out.decision_ready is True

    def test_neutral_bias_blocks_decision_ready(self):
        out = _output(bias="neutral", confidence=0.85)
        out.populate_execution_fields()
        # neutral is not directional → decision_ready False even if conf high
        assert out.decision_ready is False

    def test_unknown_bias_blocks_decision_ready(self):
        out = _output(bias="???", confidence=0.85)
        out.populate_execution_fields()
        assert out.decision_ready is False

    def test_invalid_validation_blocks_trade_eligible(self):
        out = _output(
            bias="bullish", confidence=0.85,
            validation_status="invalid",
            data_quality_flag="ok",
        )
        out.populate_execution_fields()
        assert out.decision_ready is True
        assert out.trade_eligible is False

    def test_qualified_with_caution_allows_trade(self):
        out = _output(
            bias="bullish", confidence=0.85,
            validation_status="qualified_with_caution",
            data_quality_flag="ok",
        )
        out.populate_execution_fields()
        assert out.trade_eligible is True

    def test_already_sent_blocks_trade_eligible(self):
        out = _output(
            bias="bullish", confidence=0.85,
            validation_status="ok",
            execution_status="sent",
        )
        out.populate_execution_fields()
        assert out.decision_ready is True
        assert out.trade_eligible is False  # already actioned

    def test_signal_id_derived_from_run_id(self):
        out = _output()
        out.populate_execution_fields()
        assert out.signal_id == f"sig-{out.run_id[:8]}"

    def test_signal_id_preserved_if_set(self):
        out = _output(signal_id="manual-sig-xyz")
        out.populate_execution_fields()
        assert out.signal_id == "manual-sig-xyz"

    def test_execution_status_unchanged(self):
        out = _output(execution_status="queued")
        out.populate_execution_fields()
        # The function does NOT auto-flip status — it populates metadata only.
        assert out.execution_status == "queued"

    def test_execution_mode_unchanged(self):
        out = _output(execution_mode="live")
        out.populate_execution_fields()
        assert out.execution_mode == "live"

    def test_default_execution_mode_is_none(self):
        out = _output()
        out.populate_execution_fields()
        assert out.execution_mode == "none"

    def test_schema_version_pinned(self):
        out = _output()
        # Even if someone tampered with it, populate pins to 3.5
        out.schema_version = "0.0-test"
        out.populate_execution_fields()
        assert out.schema_version == "3.5"

    def test_default_min_confidence_constant(self):
        assert EngineOutput.M5_CONFIDENCE_MIN == 0.6

    def test_custom_min_confidence_override(self):
        out = _output(confidence=0.45, data_quality_flag="ok", validation_status="ok")
        out.populate_execution_fields(min_confidence=0.4)
        assert out.decision_ready is True

    def test_intent_built_with_reason_codes(self):
        out = _output(bias="bullish")
        out.populate_execution_fields()
        codes = out.execution_intent["reason_codes"]
        assert any("validation:" in c for c in codes)
        assert any("confidence:" in c for c in codes)
        assert any("data_quality:" in c for c in codes)

    def test_returns_self_for_chaining(self):
        out = _output()
        ret = out.populate_execution_fields()
        assert ret is out

    def test_idempotent_re_populate(self):
        out = _output(bias="bullish", confidence=0.85, validation_status="ok")
        out.populate_execution_fields()
        first_signal_id = out.signal_id
        first_decision = out.execution_intent["decision"]
        # Re-populate — should not change populated fields
        out.populate_execution_fields()
        assert out.signal_id == first_signal_id
        assert out.execution_intent["decision"] == first_decision


# ── End-to-end persistence — read/write/read ──────────────────────────────


class TestPersistenceRoundTrip:
    def test_intent_survives_json_round_trip(self):
        out = _output(bias="bearish", confidence=0.75,
                       validation_status="qualified_with_caution")
        out.populate_execution_fields()
        d = out.to_dict()
        s = json.dumps(d)
        loaded = json.loads(s)
        out2 = EngineOutput.from_dict(loaded)
        assert out2.signal_id == out.signal_id
        assert out2.trade_eligible == out.trade_eligible
        assert out2.execution_intent["decision"] == "short"
        for c in out.execution_intent["reason_codes"]:
            assert c in out2.execution_intent["reason_codes"]
