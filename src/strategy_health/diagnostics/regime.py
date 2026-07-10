"""Regime / session diagnostic.

Breaks down fusion_history signals by:

* ``bias_regime`` —  trend | range | volatile | unknown
* ``session_label`` — asia | london | ny | pre_london | pre_ny | other

Then determines:

* hit-rate per bucket
* sample size per bucket
* regime-mix shift   vs first-half of the window
* session enabled/disabled flags per ``min_session_sample``
  and ``session_hit_rate_disable_threshold``

Inputs: an iterable of fusion signal dicts::

    {"bias_regime": "trend", "session_label": "london",
     "confidence": 0.7, "pnl_pct": 0.5, "decision": "long", ...}

Returns a single ``StrategyDiagnostic`` with metrics.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from ..models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SOURCE_FUSION_HISTORY,
    StrategyDiagnostic,
)


def _coerce_signal(rec: Mapping[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(rec, Mapping):
        return None
    return {
        "bias_regime": str(rec.get("bias_regime") or rec.get("regime") or "unknown"),
        "session_label": str(rec.get("session_label") or rec.get("session") or "other"),
        "decision": str(rec.get("decision") or "none"),
        "confidence": _safe_float(rec.get("confidence")),
        "pnl_pct": _safe_pnl(rec.get("pnl_pct")),
    }


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN guard
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_pnl(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _bucket_stats(signals: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for sig in signals:
        bucket = sig["bias_regime"]
        rec = out.setdefault(bucket, {"n": 0, "wins": 0, "losses": 0})
        rec["n"] += 1
        pnl = sig["pnl_pct"]
        if pnl is None:
            continue
        if pnl > 0:
            rec["wins"] += 1
        else:
            rec["losses"] += 1
    for rec in out.values():
        n = rec["n"]
        rec["hit_rate_pct"] = round((rec["wins"] / n) * 100.0, 2) if n else 0.0
    return out


def _session_stats(signals: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for sig in signals:
        sess = sig["session_label"]
        rec = out.setdefault(sess, {"n": 0, "wins": 0, "losses": 0})
        rec["n"] += 1
        pnl = sig["pnl_pct"]
        if pnl is None:
            continue
        if pnl > 0:
            rec["wins"] += 1
        else:
            rec["losses"] += 1
    for rec in out.values():
        n = rec["n"]
        rec["hit_rate_pct"] = round((rec["wins"] / n) * 100.0, 2) if n else 0.0
    return out


def _mix_proportions(records: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    if not records:
        return {}
    counter = Counter(r["bias_regime"] for r in records)
    total = sum(counter.values())
    return {k: round(v / total, 4) for k, v in counter.items()}


def _max_mix_shift(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    return max(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def compute_regime_diagnostic(
    fusion_signals: Iterable[Mapping[str, Any]] | None,
    cfg,
) -> StrategyDiagnostic:
    """Compute a single StrategyDiagnostic for regime/session health."""
    window = int(getattr(cfg, "regime_window", 30))
    min_session_sample = int(getattr(cfg, "min_session_sample", 10))
    disable_thr = float(getattr(cfg, "session_hit_rate_disable_threshold", 35.0))
    mix_shift_warn = float(getattr(cfg, "regime_mix_shift_warn_pct", 40.0))

    try:
        signals_raw = list(fusion_signals or [])
    except TypeError:
        signals_raw = []
    cleaned = [s for s in (_coerce_signal(r) for r in signals_raw) if s is not None]

    if not cleaned:
        return StrategyDiagnostic(
            name="regime",
            severity=SEVERITY_UNKNOWN,
            summary="No fusion history available — regime cannot be evaluated.",
            metrics={"window": window, "samples": 0},
            reasons=("missing_fusion_history",),
            source=SOURCE_FUSION_HISTORY,
        )

    tail = cleaned[-window:] if window > 0 else cleaned[:]
    n = len(tail)
    regime_stats = _bucket_stats(tail)
    session_stats = _session_stats(tail)

    half = max(1, n // 2)
    recent_half = tail[half:]
    prior_half = tail[:half]
    recent_mix = _mix_proportions(recent_half)
    prior_mix = _mix_proportions(prior_half)
    shift = _max_mix_shift(recent_mix, prior_mix) * 100.0

    disabled_sessions: List[str] = []
    for sess, rec in session_stats.items():
        if rec["n"] >= min_session_sample and rec["hit_rate_pct"] < disable_thr:
            disabled_sessions.append(sess)

    metrics: Dict[str, Any] = {
        "window": window,
        "samples": n,
        "regime_breakdown": regime_stats,
        "session_breakdown": session_stats,
        "regime_mix_recent": recent_mix,
        "regime_mix_prior": prior_mix,
        "regime_mix_shift_pct": round(shift, 2),
        "disabled_sessions_candidates": disabled_sessions,
        "min_session_sample": min_session_sample,
        "disable_threshold_pct": disable_thr,
        "regime_mix_shift_warn_pct": mix_shift_warn,
    }

    reasons: List[str] = []
    severity = SEVERITY_OK

    if disabled_sessions:
        severity = SEVERITY_WARN
        reasons.append("low_hit_rate_sessions")
        if any(session_stats[s]["hit_rate_pct"] < 25.0 for s in disabled_sessions):
            severity = SEVERITY_CRITICAL
            reasons.append("very_low_hit_rate_session")

    if shift >= mix_shift_warn:
        if severity == SEVERITY_OK:
            severity = SEVERITY_WARN
        reasons.append("regime_mix_shift")

    summary_text = (
        f"regime window {n} signals; sessions={sorted(session_stats.keys())}; "
        f"disabled_sessions={disabled_sessions}; "
        f"regime_mix_shift={shift:.1f}%; verdict={severity}"
    )

    return StrategyDiagnostic(
        name="regime",
        severity=severity,
        summary=summary_text,
        metrics=metrics,
        reasons=tuple(reasons) or ("within_mix_threshold",),
        source=SOURCE_FUSION_HISTORY,
    )
