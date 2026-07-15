"""
E3 Dashboard Fusion Integration Tests.

Verifies the dashboard can read and display FusionOutput JSONs across
three schema generations:
  • New V5   — all fields present (including V5 extensions)
  • Legacy V4 — V5 fields absent (safe defaults)
  • Empty     — {} or only minimal fields (graceful degradation)

Also verifies the dashboard fusion panel does NOT crash when required
fields are missing.

Coverage:
  execution_intent_str / signal_strength / regime_tag /
  summary_zh / invalidation_reason
"""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

# ── Explicit sibling import (tests/ is a package; no implicit path) ──────────
_helpers_path = Path(__file__).parent / "dashboard_helpers.py"
import importlib.util

_spec = importlib.util.spec_from_file_location("dashboard_helpers", _helpers_path)
_dh = importlib.util.module_from_spec(_spec)  # type: ignore[attr-defined]
_spec.loader.exec_module(_dh)  # type: ignore[union-attr]

load_fusion_payload   = _dh.load_fusion_payload
get_display_intent     = _dh.get_display_intent
get_signal_strength_badge = _dh.get_signal_strength_badge
get_regime_badge       = _dh.get_regime_badge


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def v5_fusion_json() -> dict:
    """New V5 fusion JSON with all fields populated."""
    return {
        "engine_name":       "fusion",
        "schema_version":   "4.0",
        "run_id":           "v5test01",
        "signal_id":        "fus-v5test01",
        "symbol":           "XAUUSD",
        "timestamp":        "2026-06-29T08:30:00",
        "timeframe":        "1D",
        "fusion_bias":      "bullish",
        "fusion_confidence": 0.82,
        "consensus_label":  "aligned",
        "conflict_label":   "none",
        "trade_candidate":  True,
        "decision_ready":   True,
        "trade_eligible":   True,
        "execution_status": "not_sent",
        "execution_mode":   "none",
        "execution_intent": {
            "decision":     "long",
            "confidence":   0.82,
            "reason_codes": ["consensus:aligned"],
        },
        # ── V5 new fields ──────────────────────────────────────────
        "execution_intent_str": "long_only",
        "signal_strength":      "strong",
        "regime_tag":          "trending",
        "invalidation_reason": None,
        "summary_zh":         "央行偏鴿派有利金價，技術面同向，共識強。",
        # ── Legacy ─────────────────────────────────────────────────
        "explanation_zh": "融合訊號：偏多（信心 82%）",
        "source_payload": {
            "scores": {
                "candlestick_score":  0.90,
                "briefing_score":     0.75,
                "agreement_score":    0.85,
                "quality_score":      0.95,
            },
        },
    }


@pytest.fixture
def legacy_v4_fusion_json() -> dict:
    """Legacy V4 fusion JSON — no V5 fields, existing dashboard format."""
    return {
        "engine_name":       "fusion",
        "schema_version":   "4.0",
        "run_id":           "v4leg01",
        "signal_id":        "fus-v4leg01",
        "symbol":           "XAUUSD",
        "timestamp":        "2026-06-27T00:00:00",
        "timeframe":        "1D",
        "fusion_bias":      "bearish",
        "fusion_confidence": 0.61,
        "consensus_label":  "partially_aligned",
        "conflict_label":   "none",
        "trade_candidate":  False,
        "decision_ready":   False,
        "trade_eligible":   False,
        "execution_status": "not_sent",
        "execution_mode":   "none",
        "execution_intent": {
            "decision":     "none",
            "confidence":   0.61,
            "reason_codes": ["consensus:partially_aligned"],
        },
        "explanation_zh": "融合訊號：偏空（信心 61%）",
        "source_payload": {
            "scores": {
                "candlestick_score":  0.65,
                "briefing_score":     0.60,
                "agreement_score":    0.55,
                "quality_score":      1.00,
            },
        },
        # ← NO V5 fields (execution_intent_str, signal_strength,
        #    regime_tag, invalidation_reason, summary_zh)
    }


@pytest.fixture
def minimal_fusion_json() -> dict:
    """Minimal JSON with only the core required fields."""
    return {
        "fusion_bias":      "neutral",
        "fusion_confidence": 0.30,
        "trade_candidate":  False,
    }


@pytest.fixture
def empty_fusion_json() -> dict:
    """Empty dict — worst-case degradation."""
    return {}


# ── Test class ───────────────────────────────────────────────────────────────


class TestDashboardFusionPayload:
    """Verify load_fusion_payload handles all schema generations."""

    # ── V5: all new fields present ─────────────────────────────────────────

    def test_v5_all_fields_preserved(self, v5_fusion_json: dict):
        """All V5 fields are read correctly when present in JSON."""
        vm = load_fusion_payload(v5_fusion_json)

        assert vm["execution_intent_str"] == "long_only"
        assert vm["signal_strength"]      == "strong"
        assert vm["regime_tag"]           == "trending"
        assert vm["invalidation_reason"]  is None
        assert vm["summary_zh"]            == "央行偏鴿派有利金價，技術面同向，共識強。"
        # Legacy fields still accessible
        assert vm["fusion_bias"]          == "bullish"
        assert vm["trade_candidate"]      is True
        assert vm["consensus_label"]       == "aligned"

    def test_v5_scores_accessible(self, v5_fusion_json: dict):
        """source_payload.scores are preserved for dashboard sub-panel."""
        vm = load_fusion_payload(v5_fusion_json)
        scores = vm["source_payload"]["scores"]
        assert scores["candlestick_score"] == 0.90
        assert scores["agreement_score"]  == 0.85
        assert scores["quality_score"]    == 0.95

    # ── Legacy V4: V5 fields absent ─────────────────────────────────────────

    def test_legacy_v4_loads_without_error(self, legacy_v4_fusion_json: dict):
        """Legacy V4 JSON with no V5 fields loads without exception."""
        vm = load_fusion_payload(legacy_v4_fusion_json)
        assert vm["fusion_bias"] == "bearish"
        assert vm["trade_candidate"] is False

    def test_legacy_v4_v5_fields_get_defaults(self, legacy_v4_fusion_json: dict):
        """V5 fields default correctly when absent from legacy JSON."""
        vm = load_fusion_payload(legacy_v4_fusion_json)

        assert vm["execution_intent_str"] is None      # None = no intent
        assert vm["signal_strength"]      == "unknown"  # safe unknown label
        assert vm["regime_tag"]           == "unknown"
        assert vm["invalidation_reason"]  is None       # Phase 1 always null
        assert vm["summary_zh"]           == ""         # empty = not available

    def test_legacy_execution_intent_dict_preserved(self, legacy_v4_fusion_json: dict):
        """Legacy execution_intent dict is not disturbed."""
        vm = load_fusion_payload(legacy_v4_fusion_json)
        assert vm["execution_intent"]["decision"] == "none"
        assert "reason_codes" in vm["execution_intent"]

    def test_legacy_schema_version_preserved(self, legacy_v4_fusion_json: dict):
        """Schema version field is preserved (not overwritten by defaults)."""
        vm = load_fusion_payload(legacy_v4_fusion_json)
        assert vm["schema_version"] == "4.0"

    # ── Minimal / Empty ─────────────────────────────────────────────────────

    def test_minimal_json_gets_all_defaults(self, minimal_fusion_json: dict):
        """JSON with only core fields still produces a valid view model."""
        vm = load_fusion_payload(minimal_fusion_json)

        # Core fields honoured
        assert vm["fusion_bias"]      == "neutral"
        assert vm["fusion_confidence"] == 0.30
        assert vm["trade_candidate"]  is False

        # All V5 fields default safely
        assert vm["execution_intent_str"] is None
        assert vm["signal_strength"]      == "unknown"
        assert vm["regime_tag"]           == "unknown"
        assert vm["invalidation_reason"]  is None
        assert vm["summary_zh"]           == ""

        # Identity defaults
        assert vm["run_id"]         == ""
        assert vm["signal_id"]      == ""
        assert vm["schema_version"] == "4.0"

    def test_empty_json_no_crash(self, empty_fusion_json: dict):
        """Empty dict {} produces a fully-populated view model with defaults."""
        vm = load_fusion_payload(empty_fusion_json)

        assert vm["fusion_bias"]      == "neutral"
        assert vm["fusion_confidence"] == 0.0
        assert vm["trade_candidate"]  is False
        assert vm["execution_intent_str"] is None
        assert vm["signal_strength"]  == "unknown"
        assert vm["regime_tag"]       == "unknown"
        assert vm["summary_zh"]       == ""

    def test_none_input_no_crash(self):
        """None input is handled gracefully (equivalent to {})."""
        vm = load_fusion_payload(None)
        assert vm["fusion_bias"] == "neutral"
        assert vm["execution_intent_str"] is None


# ── Display helpers ───────────────────────────────────────────────────────────


class TestDashboardDisplayHelpers:
    """Verify display-label helpers produce correct strings."""

    def test_get_display_intent_long_only(self):
        assert get_display_intent("long_only") == "🟢 Long Only"

    def test_get_display_intent_short_only(self):
        assert get_display_intent("short_only") == "🔴 Short Only"

    def test_get_display_intent_both(self):
        assert get_display_intent("both") == "⚪ Both Directions"

    def test_get_display_intent_none(self):
        assert get_display_intent("none") == "—"

    def test_get_display_intent_null(self):
        assert get_display_intent(None) == "—"

    def test_get_display_intent_unknown(self):
        assert "?" in get_display_intent("random_junk")

    def test_get_signal_strength_badge_strong(self):
        assert get_signal_strength_badge("strong") == "🟢 Strong"

    def test_get_signal_strength_badge_moderate(self):
        assert get_signal_strength_badge("moderate") == "🟡 Moderate"

    def test_get_signal_strength_badge_weak(self):
        assert get_signal_strength_badge("weak") == "🔵 Weak"

    def test_get_signal_strength_badge_unknown(self):
        assert get_signal_strength_badge("unknown") == "⚪ Unknown"

    def test_get_regime_badge_trending(self):
        assert get_regime_badge("trending") == "📈 Trending"

    def test_get_regime_badge_range(self):
        assert get_regime_badge("range") == "↔️ Range"

    def test_get_regime_badge_volatile(self):
        assert get_regime_badge("volatile") == "⚡ Volatile"

    def test_get_regime_badge_unknown(self):
        assert get_regime_badge("unknown") == "—"


# ── Dashboard fusion panel contract ──────────────────────────────────────────


class TestDashboardFusionContract:
    """Verify dashboard fusion panel can safely read all required keys."""

    REQUIRED_PANEL_KEYS = [
        "fusion_bias", "fusion_confidence", "trade_candidate",
        "consensus_label", "conflict_label",
    ]

    V5_PANEL_KEYS = [
        "execution_intent_str", "signal_strength", "regime_tag",
        "invalidation_reason", "summary_zh",
    ]

    IDENTITY_KEYS = [
        "run_id", "signal_id", "schema_version", "timestamp", "timeframe",
    ]

    def test_v5_json_has_all_required_panel_keys(self, v5_fusion_json: dict):
        """V5 JSON satisfies all required dashboard fusion panel keys."""
        vm = load_fusion_payload(v5_fusion_json)
        for k in self.REQUIRED_PANEL_KEYS:
            assert k in vm, f"missing required panel key: {k}"
        for k in self.V5_PANEL_KEYS:
            assert k in vm, f"missing V5 panel key: {k}"

    def test_legacy_v4_json_has_all_required_panel_keys(
        self, legacy_v4_fusion_json: dict
    ):
        """Legacy V4 JSON still satisfies required panel keys (V5 get defaults)."""
        vm = load_fusion_payload(legacy_v4_fusion_json)
        for k in self.REQUIRED_PANEL_KEYS:
            assert k in vm, f"missing required panel key: {k}"
        for k in self.V5_PANEL_KEYS:
            assert k in vm, f"missing V5 panel key: {k} (default must be set)"

    def test_empty_json_has_all_keys(self, empty_fusion_json: dict):
        """Empty JSON produces a view model with all expected keys (safe defaults)."""
        vm = load_fusion_payload(empty_fusion_json)
        all_keys = self.REQUIRED_PANEL_KEYS + self.V5_PANEL_KEYS + self.IDENTITY_KEYS
        for k in all_keys:
            assert k in vm, f"missing key: {k} (default must be present)"

    def test_no_keyerror_on_any_legacy_field_access(
        self, legacy_v4_fusion_json: dict
    ):
        """Dashboard code can access any legacy key on legacy JSON without KeyError."""
        vm = load_fusion_payload(legacy_v4_fusion_json)
        # These are what dashboard.py accesses in the fusion panel today.
        # Use "in vm" to check key presence, NOT truthiness of the value
        # (trade_candidate=False is a valid value in legacy JSON).
        assert "fusion_bias"      in vm
        assert "consensus_label"  in vm
        assert "conflict_label"   in vm
        assert "fusion_confidence" in vm
        assert "trade_candidate"  in vm   # present even when False
        assert "run_id"          in vm
        assert "signal_id"        in vm
        assert "schema_version"   in vm
        assert "source_payload"  in vm   # nested access in dashboard
        assert "explanation_zh"   in vm


# ── Real history JSON smoke tests ────────────────────────────────────────────


class TestRealHistoryFusionJSON:
    """Verify real on-disk fusion history JSONs load correctly."""

    @pytest.fixture
    def real_fusion_path(self) -> str:
        return "data/history/fusion/2026-06-27.json"

    def test_real_legacy_json_loads(self, real_fusion_path: str):
        """Pre-E1 fusion history JSON loads without error."""
        import json
        from pathlib import Path

        raw = json.loads(Path(real_fusion_path).read_text(encoding="utf-8"))
        vm = load_fusion_payload(raw)
        assert vm["fusion_bias"] in ("bullish", "bearish", "neutral")
        assert isinstance(vm["fusion_confidence"], float)

    def test_real_json_v5_fields_default(self, real_fusion_path: str):
        """Real legacy JSON has no V5 fields → all default to safe values."""
        import json
        from pathlib import Path

        raw = json.loads(Path(real_fusion_path).read_text(encoding="utf-8"))
        vm = load_fusion_payload(raw)

        # V5 fields must be defaults (not missing-key errors)
        assert vm["execution_intent_str"] is None
        assert vm["signal_strength"]      == "unknown"
        assert vm["regime_tag"]           == "unknown"
        assert vm["invalidation_reason"]  is None
        assert vm["summary_zh"]           == ""

    def test_real_json_legacy_fields_preserved(self, real_fusion_path: str):
        """Real legacy JSON preserves original field values."""
        import json
        from pathlib import Path

        raw = json.loads(Path(real_fusion_path).read_text(encoding="utf-8"))
        vm = load_fusion_payload(raw)

        # These must match the actual disk values
        assert vm["schema_version"] == "4.0"
        assert "execution_intent" in vm  # legacy dict preserved
        assert isinstance(vm["fusion_confidence"], float)