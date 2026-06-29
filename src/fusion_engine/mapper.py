"""
V4 Fusion Engine — mapper.

Pure adapter: takes upstream EngineOutput + briefing payload (or partial
briefing dict) and produces a flat FusionInput dictionary the rules/engine
can consume without further parsing.

Stays decoupled from daily_xauusd_brief.* — mapper doesn't know about
format_report, narrator, etc.  Only depends on candlestick_engine.contract.
"""

from __future__ import annotations

from typing import Optional

from .models import FusionInput


# ── Helpers ─────────────────────────────────────────────────────────────────


def _read_field(obj, *names, default=None):
    """Read first non-None attribute / dict-key from `obj` for any of `names`."""
    for n in names:
        # attribute access
        val = getattr(obj, n, None)
        if val is not None:
            return val
        # dict access (for payload dicts)
        if isinstance(obj, dict):
            val = obj.get(n)
            if val is not None:
                return val
        # dataclasses-as-dict via `to_dict()`
        to_dict = getattr(obj, "to_dict", None)
        if callable(to_dict):
            try:
                d = to_dict()
                val = d.get(n) if isinstance(d, dict) else None
                if val is not None:
                    return val
            except Exception:
                pass
    return default


def _candle_validation_status(candle_output) -> Optional[str]:
    sp = _read_field(candle_output, "source_payload", default={}) or {}
    val = sp.get("validation") or {}
    return val.get("status")


def _candle_data_quality_flag(candle_output) -> Optional[str]:
    return _read_field(candle_output, "data_quality_flag")


def _candle_briefing_payload_field(briefing_payload, key, default=None):
    """Handle both flat briefing dict and nested payload formats."""
    if briefing_payload is None:
        return default
    v = briefing_payload.get(key) if isinstance(briefing_payload, dict) else None
    return v if v is not None else default


# ── Public map builder ──────────────────────────────────────────────────────


def build_fusion_input(
    candle_output,
    briefing_payload: Optional[dict] = None,
    cfg=None,
    run_id: Optional[str] = None,
) -> FusionInput:
    """Construct a FusionInput from a candlestick EngineOutput + optional briefing.

    `briefing_payload` may be:
      • None / empty dict — graceful degrade (Confirm #4)
      • Dict with keys: `bias`/`macro_bias`, `confidence`/`macro_confidence`,
        `regime_tag`, `event_risk`, `news_sentiment`.

    `cfg` is opaque (passed through to FusionEngine). `run_id` defaults to
    the candle run_id (or generated if absent).
    """
    ri = run_id or _read_field(candle_output, "run_id", default="") or None
    return FusionInput(
        candle_output=candle_output,
        briefing_payload=briefing_payload if briefing_payload else None,
        cfg=cfg,
        run_id=ri or "",  # FusionInput owns its own uuid if blank
    )


# ── Pure readers (used by engine; exported for tests) ────────────────────────


def read_candlestick_snapshot(candle_output) -> dict:
    """Return a flat dict of candlestick signals for rule scoring."""
    sp = _read_field(candle_output, "source_payload", default={}) or {}
    val = sp.get("validation") or {}
    return {
        "bias":                  _read_field(candle_output, "bias", "technical_bias"),
        "bias_strength":         _read_field(candle_output, "bias_strength", default=0.0),
        "confidence":            _read_field(candle_output, "confidence"),
        "validation_status":     val.get("status"),
        "validation_confidence": (val.get("confidence") or {}).get("final_confidence"),
        "data_quality_flag":     _read_field(candle_output, "data_quality_flag"),
        "trade_eligible":        bool(_read_field(candle_output, "trade_eligible", default=False)),
        "decision_ready":        bool(_read_field(candle_output, "decision_ready", default=False)),
        "execution_intent":      _read_field(candle_output, "execution_intent", default={}) or {},
        "symbol":                _read_field(candle_output, "symbol", default="XAUUSD"),
        "timeframe":             _read_field(candle_output, "timeframe", default="1D"),
        "timestamp":             _read_field(candle_output, "timestamp", default=""),
        "schema_version":        _read_field(candle_output, "schema_version", default="3.5"),
        "structure_state":       sp.get("structure_state"),
        "rsi_14":                sp.get("rsi_14"),
        "atr_14":                sp.get("atr_14"),
    }


def read_briefing_snapshot(briefing_payload: Optional[dict]) -> dict:
    """Return a flat dict of briefing signals. Empty fields when payload absent."""
    if not briefing_payload:
        return {
            "present":       False,
            "bias":          None,
            "confidence":    None,
            "regime_tag":    None,
            "event_risk":    None,
            "news_sentiment": None,
        }
    # Common shapes: {"bias":..., "confidence":..., ...}
    return {
        "present":        True,
        "bias":           briefing_payload.get("bias")
                          or briefing_payload.get("macro_bias"),
        "confidence":     briefing_payload.get("confidence")
                          or briefing_payload.get("macro_confidence"),
        "regime_tag":     briefing_payload.get("regime_tag"),
        "event_risk":     briefing_payload.get("event_risk"),
        "news_sentiment": briefing_payload.get("news_sentiment"),
    }
