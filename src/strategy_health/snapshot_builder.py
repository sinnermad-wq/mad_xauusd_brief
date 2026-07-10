"""Snapshot builder — wires diagnostics + suggestions into a StrategyHealthSnapshot.

Input data sources (all read-only):
  * backtest_report   — most recent backtest evaluate JSON output
  * fusion_signals   — list of fusion_history signal dicts
  * candlestick      — candlestick latest snapshot (optional)
  * merged_signals   — combined trade records for freshness check
  * paths            — file paths for freshness mtime checks

Output: ``StrategyHealthSnapshot`` (frozen, JSON-serializable).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .suggestion import build_pending_approvals, compute_suggestions
from .diagnostics import (
    compute_cost_diagnostic,
    compute_drawdown_diagnostic,
    compute_freshness_diagnostic,
    compute_performance_diagnostic,
    compute_regime_diagnostic,
    compute_signal_drift_diagnostic,
)
from .models import (
    HEALTH_GREEN,
    HEALTH_RED,
    HEALTH_UNKNOWN,
    HEALTH_YELLOW,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    StrategyHealthConfig,
    StrategyHealthSnapshot,
    StrategySuggestion,
)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _snapshot_id() -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    h = hashlib.md5(ts.encode()).hexdigest()[:6]
    return "snap-" + ts + "-" + h


def _verdict(diagnostics: Sequence) -> str:
    if not diagnostics:
        return HEALTH_UNKNOWN
    if any(d.severity == SEVERITY_CRITICAL for d in diagnostics):
        return HEALTH_RED
    if any(d.severity == SEVERITY_WARN for d in diagnostics):
        return HEALTH_YELLOW
    if all(d.severity == SEVERITY_UNKNOWN for d in diagnostics):
        return HEALTH_UNKNOWN
    return HEALTH_GREEN


def _source_inventory(
    backtest_report,
    fusion_signals,
    candlestick,
    merged_signals,
) -> Dict[str, bool]:
    return {
        "backtest_has_data": bool(backtest_report),
        "fusion_has_data": bool(fusion_signals and len(fusion_signals) > 0),
        "candlestick_has_data": bool(candlestick),
        "merged_signals_has_data": bool(merged_signals),
    }


def _merge_all_signals(
    fusion_signals: Sequence[Mapping[str, Any]] | None,
    backtest_report: Mapping[str, Any] | None,
) -> List[Mapping[str, Any]]:
    out: List[Mapping[str, Any]] = []
    if fusion_signals:
        out.extend(fusion_signals)
    trades: List[Any] = []
    if backtest_report:
        trades = (
            backtest_report.get("trades")
            or backtest_report.get("outcomes")
            or (backtest_report.get("stats", {}).get("trades") if isinstance(backtest_report, Mapping) else None)
            or []
        )
    if trades:
        out.extend(trades)
    return out


def build_health_snapshot(
    *,
    backtest_report: Mapping[str, Any] | None = None,
    fusion_signals: Sequence[Mapping[str, Any]] | None = None,
    candlestick_snapshot: Mapping[str, Any] | None = None,
    fusion_history_path: str | None = None,
    backtest_report_path: str | None = None,
    candlestick_snapshot_path: str | None = None,
    cfg: StrategyHealthConfig | None = None,
    approval_file_path: str = "data/strategy_health/approvals.json",
    now_utc: datetime | None = None,
) -> StrategyHealthSnapshot:
    """Compute a full health snapshot from all data sources.

    All I/O is read-only. Returns a frozen StrategyHealthSnapshot.
    No execution side-effects.
    """
    if cfg is None:
        cfg = StrategyHealthConfig()

    warnings: List[str] = []

    # Run all 6 diagnostics
    perf = compute_performance_diagnostic(backtest_report, cfg)
    if perf.severity == SEVERITY_UNKNOWN:
        warnings.append("performance_unknown_no_backtest_data")

    regime = compute_regime_diagnostic(fusion_signals, cfg)
    if regime.severity == SEVERITY_UNKNOWN:
        warnings.append("regime_unknown_no_fusion_history")

    cost_diag = compute_cost_diagnostic(backtest_report, cfg)
    signal_diag = compute_signal_drift_diagnostic(fusion_signals, cfg)
    drawdown_diag = compute_drawdown_diagnostic(backtest_report, cfg)
    freshness = compute_freshness_diagnostic(
        fusion_history_path=fusion_history_path,
        backtest_report_path=backtest_report_path,
        candlestick_snapshot_path=candlestick_snapshot_path,
        merged_signals=_merge_all_signals(fusion_signals, backtest_report),
        cfg=cfg,
        now_utc=now_utc,
    )

    diagnostics = [perf, regime, cost_diag, signal_diag, drawdown_diag, freshness]

    # Build suggestions
    sugs = compute_suggestions(
        diagnostics,
        raw_perf=backtest_report,
        raw_regime=None,
        raw_cost=backtest_report,
        raw_signal=None,
        raw_drawdown=backtest_report,
        cfg=cfg,
    )

    # Build pending approvals
    pending_approvals = build_pending_approvals(sugs, approval_file_path)

    health_status = _verdict(diagnostics)

    return StrategyHealthSnapshot(
        snapshot_id=_snapshot_id(),
        generated_at=_now(),
        health_status=health_status,
        diagnostics=tuple(diagnostics),
        suggestions=sugs,
        pending_approvals=pending_approvals,
        config_snapshot=cfg.to_dict(),
        warnings=tuple(warnings),
        source_inventory=_source_inventory(backtest_report, fusion_signals, candlestick_snapshot, None),
    )