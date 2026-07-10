"""Suggestion engine — maps diagnostics to ranked StrategySuggestions.

Manual-only: engine only *emits* suggestions. It never auto-applies,
auto-adjusts parameters, or connects to execution layers.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Tuple

from .models import (
    APPROVAL_PENDING,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SUG_DISABLE_SESSION,
    SUG_KEEP_RUNNING,
    SUG_PAUSE_STRATEGY,
    SUG_REDUCE_SIZE,
    SUG_REVALIDATE_BACKTEST,
    SUG_REVIEW_PARAMETERS,
    SUG_TIGHTEN_FILTER,
    SUG_WATCH_ONLY,
    StrategyApprovalState,
    StrategyDiagnostic,
    StrategySuggestion,
)


# Priority: lower = more urgent. 1 = highest.
_PRIORITY = {
    SUG_PAUSE_STRATEGY: 1,
    SUG_REVALIDATE_BACKTEST: 2,
    SUG_DISABLE_SESSION: 3,
    SUG_REDUCE_SIZE: 4,
    SUG_TIGHTEN_FILTER: 5,
    SUG_REVIEW_PARAMETERS: 6,
    SUG_WATCH_ONLY: 7,
    SUG_KEEP_RUNNING: 8,
}


def _uid(kind: str, seed: str) -> str:
    h = hashlib.sha1((kind + seed).encode()).hexdigest()[:8]
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    return "sug-" + ts + "-" + h


def _emit(
    kind: str,
    diagnostic_name: str,
    title: str,
    rationale: str,
    metrics: Dict[str, Any],
    actions: Tuple[str, ...],
    cfg,
) -> StrategySuggestion:
    prio = _PRIORITY.get(kind, 8)
    sid = _uid(kind, diagnostic_name + str(prio))
    return StrategySuggestion(
        suggestion_id=sid,
        kind=kind,
        priority=prio,
        title=title,
        rationale=rationale,
        diagnostic_name=diagnostic_name,
        metrics=metrics,
        actions=actions,
    )


def _diag_by_name(diags: List[StrategyDiagnostic], name: str) -> StrategyDiagnostic | None:
    for d in diags:
        if d.name == name:
            return d
    return None


def _is_critical(d: StrategyDiagnostic | None) -> bool:
    return d is not None and d.severity == SEVERITY_CRITICAL


def _is_warn(d: StrategyDiagnostic | None) -> bool:
    return d is not None and d.severity == SEVERITY_WARN


def _metric(d: StrategyDiagnostic | None, key: str, default: Any = None) -> Any:
    if d is None:
        return default
    return d.metrics.get(key, default)


def compute_suggestions(
    diagnostics: List[StrategyDiagnostic],
    raw_perf: Mapping[str, Any] | None = None,
    raw_regime: Mapping[str, Any] | None = None,
    raw_cost: Mapping[str, Any] | None = None,
    raw_signal: Mapping[str, Any] | None = None,
    raw_drawdown: Mapping[str, Any] | None = None,
    cfg=None,
) -> Tuple[StrategySuggestion, ...]:
    """Given 6 diagnostics + raw metric dicts, return ranked suggestions.

    All decisions are read-only analytical output. No execution side-effects.
    """
    perf = _diag_by_name(diagnostics, "performance")
    regime = _diag_by_name(diagnostics, "regime")
    cost = _diag_by_name(diagnostics, "cost")
    signal = _diag_by_name(diagnostics, "signal")
    drawdown = _diag_by_name(diagnostics, "drawdown")
    freshness = _diag_by_name(diagnostics, "freshness")

    suggestions: List[StrategySuggestion] = []
    crit_count = sum(
        1 for d in diagnostics if d.severity == SEVERITY_CRITICAL
    )
    warn_count = sum(
        1 for d in diagnostics if d.severity == SEVERITY_WARN
    )

    # ── 1. pause_strategy ────────────────────────────────────────────────
    if crit_count >= 2:
        suggestions.append(_emit(
            SUG_PAUSE_STRATEGY, "multiple",
            "Pause Strategy — Multiple Critical Diagnostics",
            ("2+ diagnostics at critical severity detected. "
             "Manual review required before continuing. "
             "No auto-adjustment is applied."),
            {
                "critical_count": crit_count,
                "warn_count": warn_count,
                "diagnostics": [d.name for d in diagnostics if d.severity == SEVERITY_CRITICAL],
            },
            (
                "1. Review all critical diagnostics.",
                "2. Document findings in data/engine_reviews.csv.",
                "3. Re-enable only after human approval.",
            ),
            cfg,
        ))

    # ── 2. revalidate_backtest ───────────────────────────────────────────
    cost_drag = _metric(cost, "cost_drag_pct", 0.0)
    perf_hit = _metric(perf, "hit_rate_pct", 50.0)
    baseline = float(getattr(cfg, "expected_baseline_hit_rate", 55.0) if cfg else 55.0)
    cost_crit = float(getattr(cfg, "cost_drag_critical_pct", 30.0) if cfg else 30.0)
    if cost_drag >= cost_crit and perf_hit < baseline - 10.0:
        suggestions.append(_emit(
            SUG_REVALIDATE_BACKTEST, "cost",
            "Revalidate Backtest — Cost Drag + Win-Rate Degradation",
            ("High execution cost drag combined with declining hit-rate "
             "suggests the strategy parameters may no longer match "
             "prevailing market conditions."),
            {
                "cost_drag_pct": cost_drag,
                "hit_rate_pct": perf_hit,
                "baseline_hit_rate_pct": baseline,
                "cost_critical_threshold": cost_crit,
            },
            (
                "1. Re-run backtest over latest 90-day window.",
                "2. Compare hit-rate and expectancy vs historical baseline.",
                "3. Update parameters only after human review.",
            ),
            cfg,
        ))

    # ── 3. disable_session ──────────────────────────────────────────────
    disabled = _metric(regime, "disabled_sessions_candidates", [])
    if disabled:
        for sess in disabled:
            suggestions.append(_emit(
                SUG_DISABLE_SESSION, "regime",
                "Disable Session — " + sess.upper() + " Low Hit-Rate",
                ("Session '" + sess + "' shows hit-rate below disable "
                 "threshold across the regime window. "
                 "Manual decision to exclude this session window is recommended."),
                {
                    "session": sess,
                    "hit_rate_pct": _metric(
                        regime, ("session_breakdown/" + sess + "/hit_rate_pct"), 0.0
                    ),
                    "disable_threshold_pct": _metric(
                        regime, "disable_threshold_pct", 35.0
                    ),
                    "sample_size": _metric(
                        regime, ("session_breakdown/" + sess + "/n"), 0
                    ),
                },
                (
                    "1. Confirm sample size >= minimum threshold.",
                    "2. Manually disable '" + sess + "' in fusion rules.",
                    "3. Re-evaluate after next 20 signals.",
                ),
                cfg,
            ))

    # ── 4. tighten_filter ──────────────────────────────────────────────
    ratio = _metric(signal, "low_conf_recent_vs_prior_ratio", 1.0)
    drift_thr = float(getattr(cfg, "low_conf_drift_ratio_warn", 0.65) if cfg else 0.65)
    if ratio != "inf" and float(ratio) < drift_thr:
        suggestions.append(_emit(
            SUG_TIGHTEN_FILTER, "signal",
            "Tighten Filter — Low-Confidence Signal Hit-Rate Declining",
            ("Low-confidence signal subset hit-rate has dropped below "
             "the acceptable drift ratio. Consider tightening the "
             "low-confidence filter threshold or reducing position "
             "size for low-confidence signals."),
            {
                "low_conf_hit_rate_ratio": ratio,
                "drift_warn_threshold": drift_thr,
                "recent_low_conf_hit_rate_pct": _metric(
                    signal, "recent_low_conf_hit_rate_pct", 0.0
                ),
                "prior_low_conf_hit_rate_pct": _metric(
                    signal, "prior_low_conf_hit_rate_pct", 0.0
                ),
            },
            (
                "1. Increase fusion confidence threshold for low-confidence bucket.",
                "2. Reduce size for signals below the filter.",
                "3. Document the filter change per rule_change_protocol.md.",
            ),
            cfg,
        ))

    # ── 5. reduce_size ──────────────────────────────────────────────────
    max_dd = _metric(drawdown, "max_dd_pct", 0.0)
    dd_warn = float(getattr(cfg, "drawdown_warn_pct", 7.5) if cfg else 7.5)
    dd_crit = float(getattr(cfg, "drawdown_critical_pct", 15.0) if cfg else 15.0)
    if max_dd >= dd_warn or cost_drag >= dd_warn:
        suggestions.append(_emit(
            SUG_REDUCE_SIZE, "drawdown",
            "Reduce Position Size — Drawdown / Cost Stress",
            ("Current max drawdown or cost drag exceeds warning threshold. "
             "Reducing position size lowers exposure while the root cause "
             "is investigated."),
            {
                "max_dd_pct": max_dd,
                "cost_drag_pct": cost_drag,
                "drawdown_warn_threshold_pct": dd_warn,
                "drawdown_critical_threshold_pct": dd_crit,
            },
            (
                "1. Reduce position size by 25-50%.",
                "2. Log the change in data/engine_reviews.csv.",
                "3. Re-evaluate after 10 new signals.",
            ),
            cfg,
        ))

    # ── 6. review_parameters ─────────────────────────────────────────────
    shift = _metric(regime, "regime_mix_shift_pct", 0.0)
    shift_warn = float(getattr(cfg, "regime_mix_shift_warn_pct", 40.0) if cfg else 40.0)
    if shift >= shift_warn:
        suggestions.append(_emit(
            SUG_REVIEW_PARAMETERS, "regime",
            "Review Parameters — Regime Mix Shift Detected",
            ("Regime composition has shifted by more than " + str(shift_warn)
             + "% between the recent and prior halves of the analysis window. "
             "Strategy parameters calibrated for the prior regime may be "
             "mismatched to current conditions."),
            {
                "regime_mix_shift_pct": shift,
                "shift_warn_threshold_pct": shift_warn,
            },
            (
                "1. Review fusion regime classification rules.",
                "2. Assess whether current bias_regime thresholds need updating.",
                "3. Follow rule_change_protocol.md before any parameter change.",
            ),
            cfg,
        ))

    # ── 7. watch_only (yellow health) ──────────────────────────────────
    if suggestions:
        pass  # already have higher-urgency suggestions
    elif _is_warn(perf) or _is_warn(regime) or _is_warn(cost) or \
            _is_warn(signal) or _is_warn(drawdown) or _is_warn(freshness):
        suggestions.append(_emit(
            SUG_WATCH_ONLY, "health",
            "Watch Only — At Least One Warning Diagnostic",
            ("One or more diagnostics are at warn severity. "
             "Strategy continues but should be monitored more closely. "
             "No automatic changes are applied."),
            {
                "warn_diagnostics": [d.name for d in diagnostics if d.severity == SEVERITY_WARN],
                "warn_count": warn_count,
            },
            ("Monitor next 10 signals closely.",),
            cfg,
        ))

    # ── 8. keep_running (green / no issues) ────────────────────────────
    if not suggestions:
        suggestions.append(_emit(
            SUG_KEEP_RUNNING, "health",
            "Keep Running — All Diagnostics Within Normal Range",
            ("All 6 diagnostic categories are within acceptable thresholds. "
             "No adjustments recommended at this time."),
            {
                "diagnostics_ok": [d.name for d in diagnostics if d.severity == SEVERITY_OK],
                "diagnostics_unknown": [
                    d.name for d in diagnostics if d.severity == SEVERITY_UNKNOWN
                ],
            },
            ("Continue monitoring per normal schedule.",),
            cfg,
        ))

    # Sort by priority, stable within same priority
    suggestions.sort(key=lambda s: s.priority)
    return tuple(suggestions)


def build_pending_approvals(
    suggestions: Tuple[StrategySuggestion, ...],
    approval_file_path: str,
) -> Tuple[StrategyApprovalState, ...]:
    """Build pending approval states for all suggestions.

    This does NOT write the file — caller writes it.
    Manual review + edit is required before any resolution.
    """
    import json
    import os

    existing: Dict[str, Any] = {}
    if os.path.exists(approval_file_path):
        try:
            existing = json.loads(open(approval_file_path).read())
        except Exception:
            existing = {}

    now = datetime.now(tz=timezone.utc).isoformat()
    pending: List[StrategyApprovalState] = []

    for sug in suggestions:
        key = sug.suggestion_id
        if key in existing:
            state = existing[key]
            status = state.get("status", APPROVAL_PENDING)
        else:
            status = APPROVAL_PENDING
        pending.append(StrategyApprovalState(
            suggestion_id=sug.suggestion_id,
            kind=sug.kind,
            status=status,
            created_at=now,
            updated_at=now,
        ))

    return tuple(pending)