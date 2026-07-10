"""Signal quality drift diagnostic.

Compares recent-half vs prior-half hit-rates for the *low-confidence*
bucket (signals with ``confidence < 0.55`` default). Also exposes
overall hit-rate shift across the same window.

Inputs: fusion signals::

    {"confidence": 0.42, "pnl_pct": 0.3, "decision": "long", ...}

Returns one ``StrategyDiagnostic``.  Triggers include:

* ``low_conf_drift`` — recent-half low-conf hit-rate < baseline ratio
* ``overall_drift``  — overall hit-rate shift > 10pp
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SOURCE_FUSION_HISTORY,
    StrategyDiagnostic,
)
from .regime import _safe_float  # noqa: F401  (reused)


def _safe_pnl(v: Any) -> Optional[float]:
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


def _coerce(rec: Mapping[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(rec, Mapping):
        return None
    conf = _safe_float(rec.get("confidence"))
    pnl = _safe_pnl(rec.get("pnl_pct"))
    if pnl is None:
        return None
    return {
        "confidence": conf if conf is not None else 0.5,
        "pnl_pct": pnl,
        "decision": str(rec.get("decision") or "none"),
    }


def _hit_rate(rows: Sequence[Mapping[str, Any]]) -> Tuple[float, int]:
    if not rows:
        return 0.0, 0
    wins = sum(1 for r in rows if r["pnl_pct"] > 0)
    return (wins / len(rows)) * 100.0, len(rows)


def compute_signal_drift_diagnostic(
    fusion_signals: Iterable[Mapping[str, Any]] | None,
    cfg,
) -> StrategyDiagnostic:
    window = int(getattr(cfg, "signal_window", 50))
    low_conf_thr = 0.55
    drift_warn = float(getattr(cfg, "low_conf_drift_ratio_warn", 0.65))
    overall_drift_warn_pp = 10.0
    overall_drift_crit_pp = 20.0

    try:
        raw = list(fusion_signals or [])
    except TypeError:
        raw = []
    cleaned = [s for s in (_coerce(r) for r in raw) if s is not None]

    if not cleaned:
        return StrategyDiagnostic(
            name="signal",
            severity=SEVERITY_UNKNOWN,
            summary="No fusion history — signal drift cannot be evaluated.",
            metrics={"window": window, "samples": 0},
            reasons=("missing_fusion_history",),
            source=SOURCE_FUSION_HISTORY,
        )

    tail = cleaned[-window:] if window > 0 else cleaned[:]
    n = len(tail)
    half = max(1, n // 2)
    recent = tail[half:]
    prior = tail[:half]

    recent_low = [r for r in recent if r["confidence"] < low_conf_thr]
    prior_low = [r for r in prior if r["confidence"] < low_conf_thr]

    recent_low_hr, _ = _hit_rate(recent_low)
    prior_low_hr, _ = _hit_rate(prior_low)
    recent_overall_hr, _ = _hit_rate(recent)
    prior_overall_hr, _ = _hit_rate(prior)

    ratio = (
        (recent_low_hr / prior_low_hr)
        if prior_low_hr > 0
        else (1.0 if recent_low_hr == 0 else float("inf"))
    )

    metrics: Dict[str, Any] = {
        "window": window,
        "samples": n,
        "low_confidence_threshold": low_conf_thr,
        "recent_overall_hit_rate_pct": round(recent_overall_hr, 2),
        "prior_overall_hit_rate_pct": round(prior_overall_hr, 2),
        "recent_low_conf_hit_rate_pct": round(recent_low_hr, 2),
        "prior_low_conf_hit_rate_pct": round(prior_low_hr, 2),
        "low_conf_recent_vs_prior_ratio": (
            round(ratio, 2) if ratio != float("inf") else "inf"
        ),
        "drift_warn_ratio": drift_warn,
        "overall_drift_warn_pp": overall_drift_warn_pp,
        "overall_drift_crit_pp": overall_drift_crit_pp,
    }

    reasons: List[str] = []
    severity = SEVERITY_OK

    if prior_low_hr > 0 and ratio < drift_warn:
        severity = SEVERITY_WARN
        reasons.append("low_conf_hit_rate_decline")

    overall_delta = recent_overall_hr - prior_overall_hr
    if overall_delta <= -overall_drift_crit_pp:
        if severity in {SEVERITY_OK, SEVERITY_WARN}:
            severity = SEVERITY_CRITICAL
        reasons.append("overall_hit_rate_collapse")
    elif overall_delta <= -overall_drift_warn_pp:
        if severity == SEVERITY_OK:
            severity = SEVERITY_WARN
        reasons.append("overall_hit_rate_decline")

    summary_text = (
        f"signal window {n}; overall shift {overall_delta:+.2f}pp; "
        f"low-conf recent {recent_low_hr:.2f}% vs prior {prior_low_hr:.2f}% "
        f"(ratio {ratio if ratio != float('inf') else 'inf'}); "
        f"verdict={severity}"
    )

    return StrategyDiagnostic(
        name="signal",
        severity=severity,
        summary=summary_text,
        metrics=metrics,
        reasons=tuple(reasons) or ("signal_within_drift_threshold",),
        source=SOURCE_FUSION_HISTORY,
    )
