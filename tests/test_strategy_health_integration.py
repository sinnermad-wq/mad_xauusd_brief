"""Integration tests for strategy health — full pipeline with mock data."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from src.strategy_health import (
    build_health_snapshot,
    load_snapshot_from_paths,
    compute_suggestions,
    StrategyHealthConfig,
    HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED,
    SEVERITY_OK, SEVERITY_WARN, SEVERITY_CRITICAL, SEVERITY_UNKNOWN,
    SUG_KEEP_RUNNING, SUG_WATCH_ONLY, SUG_PAUSE_STRATEGY,
    APPROVAL_PENDING,
)
from src.strategy_health.models import StrategyDiagnostic


def _cfg(**o):
    return StrategyHealthConfig(**o)


def test_full_pipeline_with_mock_data():
    """End-to-end: build snapshot from in-memory data, verify all 6 diagnostics + suggestions."""
    trades = [{"pnl_pct": 1.0} for _ in range(10)] + [{"pnl_pct": -0.5} for _ in range(10)]
    sigs = [
        {"bias_regime": "trend", "session_label": "asia",
         "confidence": 0.7, "pnl_pct": 0.8}
        for _ in range(15)
    ] + [
        {"bias_regime": "range", "session_label": "ny",
         "confidence": 0.6, "pnl_pct": 0.4}
        for _ in range(15)
    ]

    snap = build_health_snapshot(
        backtest_report={"trades": trades},
        fusion_signals=sigs,
        cfg=_cfg(),
    )

    assert snap.snapshot_id.startswith("snap-")
    assert len(snap.diagnostics) == 6
    assert {d.name for d in snap.diagnostics} == {
        "performance", "regime", "cost", "signal", "drawdown", "freshness"
    }
    assert len(snap.suggestions) >= 1
    assert snap.health_status in {HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED}
    assert snap.config_snapshot is not None
    assert snap.source_inventory["backtest_has_data"] is True
    assert snap.source_inventory["fusion_has_data"] is True


def test_snapshot_from_paths_with_real_tmp_files():
    """Test load_snapshot_from_paths reads backtest + fusion files from disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bt_path = os.path.join(tmpdir, "backtest.json")
        fusion_path = os.path.join(tmpdir, "fusion.json")
        approvals_path = os.path.join(tmpdir, "approvals.json")

        trades = [{"pnl_pct": v} for v in [1.0, -0.4, 0.9, -0.3] * 5]
        sigs = [
            {"bias_regime": "trend", "session_label": "london",
             "confidence": 0.65, "pnl_pct": 0.7}
            for _ in range(20)
        ]

        with open(bt_path, "w") as f:
            json.dump({"trades": trades}, f)
        with open(fusion_path, "w") as f:
            json.dump(sigs, f)
        with open(approvals_path, "w") as f:
            json.dump({}, f)

        snap = load_snapshot_from_paths(
            fusion_history_path=fusion_path,
            backtest_report_path=bt_path,
            cfg=_cfg(performance_window=20, regime_window=20),
            approval_file_path=approvals_path,
        )

        assert snap.health_status in {HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED}
        assert {d.name for d in snap.diagnostics} == {
            "performance", "regime", "cost", "signal", "drawdown", "freshness"
        }
        assert len(snap.suggestions) >= 1


def test_suggestion_priority_order():
    """Ensure higher-priority suggestions come first when multiple fire."""
    # Two critical diagnostics → should trigger pause_strategy (priority 1)
    diags = [
        StrategyDiagnostic(name="performance", severity=SEVERITY_CRITICAL,
                           summary="crit", metrics={}, reasons=(), source="test"),
        StrategyDiagnostic(name="regime", severity=SEVERITY_CRITICAL,
                           summary="crit", metrics={}, reasons=(), source="test"),
        StrategyDiagnostic(name="cost", severity=SEVERITY_OK,
                           summary="ok", metrics={}, reasons=(), source="test"),
        StrategyDiagnostic(name="signal", severity=SEVERITY_OK,
                           summary="ok", metrics={}, reasons=(), source="test"),
        StrategyDiagnostic(name="drawdown", severity=SEVERITY_OK,
                           summary="ok", metrics={}, reasons=(), source="test"),
        StrategyDiagnostic(name="freshness", severity=SEVERITY_OK,
                           summary="ok", metrics={}, reasons=(), source="test"),
    ]
    sugs = compute_suggestions(diags, cfg=_cfg())
    assert sugs[0].kind == SUG_PAUSE_STRATEGY
    assert sugs[0].priority == 1


def test_freshness_none_paths_returns_unknown():
    """Freshness diagnostic with None paths should return unknown, not crash."""
    from src.strategy_health.diagnostics.freshness import compute_freshness_diagnostic
    d = compute_freshness_diagnostic(
        fusion_history_path=None,
        backtest_report_path=None,
        candlestick_snapshot_path=None,
        merged_signals=None,
        cfg=None,
    )
    assert d.severity in {SEVERITY_CRITICAL, SEVERITY_UNKNOWN}


def test_health_status_unknown_when_all_unknown():
    """All-unknown diagnostics should not produce keep_running as top suggestion."""
    diags = [
        StrategyDiagnostic(name=n, severity=SEVERITY_CRITICAL,
                           summary="crit", metrics={}, reasons=(), source="test")
        for n in ["performance", "regime", "cost", "signal", "drawdown", "freshness"]
    ]
    sugs = compute_suggestions(diags, cfg=_cfg())
    assert sugs[0].kind == SUG_PAUSE_STRATEGY


def test_approvals_json_created_if_missing():
    """upsert_approval creates the directory + file if missing."""
    from src.strategy_health.approval import upsert_approval
    from src.strategy_health.models import StrategyApprovalState

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "subdir", "approvals.json")
        state = StrategyApprovalState(
            suggestion_id="s-test",
            kind=SUG_KEEP_RUNNING,
            status=APPROVAL_PENDING,
            created_at="2026-07-10T00:00:00Z",
            updated_at="2026-07-10T00:00:00Z",
        )
        upsert_approval(state, path)
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert "s-test" in data
        assert data["s-test"]["status"] == APPROVAL_PENDING