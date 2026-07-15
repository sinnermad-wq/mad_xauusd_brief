"""
Dashboard Fusion Payload Helpers — V5 extended.

Provides a clean read-layer for the dashboard's fusion panel.
All field access goes through here so the dashboard stays
backward-compatible with old history JSONs.

Responsibilities:
  1. Read fusion JSON (new or legacy) → Python dict (dashboard view model)
  2. Normalise V5 fields with safe defaults
  3. Expose display-oriented computed values

Does NOT:
  - Talk to Streamlit (no st. calls)
  - Call any engine (no recompute)
  - Write files
"""

from __future__ import annotations

from typing import Any, Optional


# ── Field normalisation defaults ──────────────────────────────────────────────

# All fields that load_fusion_payload reads, with safe defaults.
# Covers V5 extensions + all legacy keys needed by the dashboard panel.
_FUSION_DEFAULTS: dict[str, Any] = {
    # Core decision
    "fusion_bias":       "neutral",
    "fusion_confidence": 0.0,
    "trade_candidate":   False,
    # Labels
    "consensus_label":   "insufficient_context",
    "conflict_label":    "none",
    # V5 new fields
    "execution_intent_str": None,
    "signal_strength":      "unknown",
    "regime_tag":           "unknown",
    "invalidation_reason":  None,
    "summary_zh":           "",
    # Identity
    "run_id":         "",
    "signal_id":      "",
    "schema_version": "4.0",
    "timestamp":      "",
    "timeframe":      "1D",
    "symbol":         "XAUUSD",
    # Legacy
    "explanation_zh":  "",
    "source_payload": {},
    "execution_intent": {},
}


def _normalise(raw: dict) -> dict:
    """Fill in defaults for any missing keys (backward compat)."""
    out = dict(raw) if raw else {}
    for k, v in _FUSION_DEFAULTS.items():
        out.setdefault(k, v)
    return out


# ── Public API ───────────────────────────────────────────────────────────────


def load_fusion_payload(raw_json: dict) -> dict:
    """Parse a fusion history JSON dict into a dashboard-ready view model.

    Handles:
      • New V5 JSON (all fields present)
      • Legacy V4 JSON (missing V5 fields → safe defaults)
      • Partial JSON (some keys missing → safe defaults)
      • Empty dict ({} → all defaults)

    Returns a dict with guaranteed keys for the dashboard fusion panel.
    """
    if not raw_json:
        return _normalise({})

    # Ensure all V5 defaults are present
    normalised = _normalise(raw_json)

    return {
        # ── Core decision ───────────────────────────────────────────────
        "fusion_bias":      normalised.get("fusion_bias", "neutral"),
        "fusion_confidence": float(normalised.get("fusion_confidence", 0)),
        "trade_candidate":  bool(normalised.get("trade_candidate", False)),

        # ── Labels ──────────────────────────────────────────────────────
        "consensus_label":  normalised.get("consensus_label", "insufficient_context"),
        "conflict_label":   normalised.get("conflict_label", "none"),

        # ── V5 new fields ─────────────────────────────────────────────
        # String intent: "long_only" | "short_only" | "both" | "none" | None
        "execution_intent_str": normalised.get("execution_intent_str"),
        # Label: "strong" | "moderate" | "weak" | "unknown"
        "signal_strength":   normalised.get("signal_strength", "unknown"),
        # Label: "trending" | "range" | "volatile" | "unknown"
        "regime_tag":        normalised.get("regime_tag", "unknown"),
        # Always null in Phase 1
        "invalidation_reason": normalised.get("invalidation_reason"),
        # LLM summary or empty
        "summary_zh":        normalised.get("summary_zh", ""),

        # ── Identity ───────────────────────────────────────────────────
        "run_id":       normalised.get("run_id", ""),
        "signal_id":    normalised.get("signal_id", ""),
        "schema_version": normalised.get("schema_version", "4.0"),
        "timestamp":    normalised.get("timestamp", ""),
        "timeframe":    normalised.get("timeframe", "1D"),
        "symbol":       normalised.get("symbol", "XAUUSD"),

        # ── Scores (from source_payload) ──────────────────────────────
        "source_payload":  normalised.get("source_payload", {}),
        "explanation_zh":  normalised.get("explanation_zh", ""),

        # ── Legacy execution_intent dict (preserved) ───────────────────
        "execution_intent": normalised.get("execution_intent", {}),
    }


def get_display_intent(execution_intent_str: Optional[str]) -> str:
    """Human-readable intent label for the dashboard."""
    if execution_intent_str is None:
        return "—"
    labels = {
        "long_only":  "🟢 Long Only",
        "short_only": "🔴 Short Only",
        "both":       "⚪ Both Directions",
        "none":       "—",
    }
    return labels.get(execution_intent_str, f"? ({execution_intent_str})")


def get_signal_strength_badge(strength: str) -> str:
    """Coloured badge label for signal_strength."""
    badges = {
        "strong":   "🟢 Strong",
        "moderate": "🟡 Moderate",
        "weak":     "🔵 Weak",
        "unknown":  "⚪ Unknown",
    }
    return badges.get(strength, f"? ({strength})")


def get_regime_badge(regime: str) -> str:
    """Coloured badge label for regime_tag."""
    badges = {
        "trending": "📈 Trending",
        "range":     "↔️ Range",
        "volatile":  "⚡ Volatile",
        "unknown":   "—",
    }
    return badges.get(regime, f"? ({regime})")