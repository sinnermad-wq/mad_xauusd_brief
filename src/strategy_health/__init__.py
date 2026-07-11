"""Strategy Health Monitor + Suggestion Engine v1.

Public entry points::

    from src.strategy_health import load_latest_snapshot, load_snapshot_from_paths

    snap = load_latest_snapshot()

    # Record current snapshot to history
    from src.strategy_health.history import append_snapshot_to_history
    append_snapshot_to_history(snap)

    # Load history + compute trends
    from src.strategy_health.history import (
        load_history_entries,
        compute_trend_window,
        compute_all_trend_windows,
        generate_trend_report_markdown,
        generate_trend_report_csv,
        snapshot_to_history_entry,
    )
    entries = load_history_entries(limit=30)
    t7  = compute_trend_window(days=7)
    all_trends = compute_all_trend_windows()   # 7d + 14d + 30d
    print(generate_trend_report_markdown(all_trends))

    # Suggestions
    from src.strategy_health import compute_suggestions
    sugs = compute_suggestions(snap.diagnostics)

    # Read-only review summary (for dashboard)
    from src.strategy_health import build_review_report_all_windows
    reports = build_review_report_all_windows(snap)
    md_7d = reports["7d"].to_markdown()

See docs/health/strategy_health_engine_v1.md for full documentation.
"""
from __future__ import annotations

from .approval import load_approvals, upsert_approval, write_approvals
from .history import (
    append_entry_to_history,
    append_snapshot_to_history,
    compute_all_trend_windows,
    compute_trend_window,
    generate_trend_report_csv,
    generate_trend_report_markdown,
    load_history_entries,
    snapshot_to_history_entry,
)
from .history_models import (
    ApprovalTrend,
    DiagnosticTrend,
    HealthHistoryEntry,
    HealthTrendMetrics,
    RegimeTrend,
    SuggestionTrend,
    TrendDirection,
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
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_SUPERSEDED,
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
    StrategyHealthConfig,
    StrategyHealthSnapshot,
    StrategySuggestion,
)
from .snapshot_builder import build_health_snapshot
from .snapshot_loader import load_latest_snapshot, load_snapshot_from_paths
from .suggestion import build_pending_approvals, compute_suggestions
from .review_actions_queue import (
    ReviewAction,
    ReviewActionsQueue,
    build_review_actions_queue,
    build_review_actions_queue_all_windows,
)
from .review_report import (
    ReviewFinding,
    ReviewReport,
    ReviewSection,
    build_review_report,
    build_review_report_all_windows,
)

__all__ = [
    # Snapshot (core)
    "load_latest_snapshot",
    "load_snapshot_from_paths",
    "build_health_snapshot",
    # Models
    "StrategyHealthSnapshot",
    "StrategyDiagnostic",
    "StrategySuggestion",
    "StrategyApprovalState",
    "StrategyHealthConfig",
    "HEALTH_GREEN",
    "HEALTH_YELLOW",
    "HEALTH_RED",
    "HEALTH_UNKNOWN",
    # Approval constants
    "APPROVAL_PENDING",
    "APPROVAL_APPROVED",
    "APPROVAL_REJECTED",
    "APPROVAL_SUPERSEDED",
    "SEVERITY_OK",
    "SEVERITY_WARN",
    "SEVERITY_CRITICAL",
    "SEVERITY_UNKNOWN",
    # Approval
    "load_approvals",
    "upsert_approval",
    "write_approvals",
    # History
    "append_snapshot_to_history",
    "append_entry_to_history",
    "snapshot_to_history_entry",
    "load_history_entries",
    "compute_trend_window",
    "compute_all_trend_windows",
    "generate_trend_report_markdown",
    "generate_trend_report_csv",
    # History models
    "HealthHistoryEntry",
    "HealthTrendMetrics",
    "DiagnosticTrend",
    "SuggestionTrend",
    "ApprovalTrend",
    "RegimeTrend",
    "TrendDirection",
    # Suggestions
    "compute_suggestions",
    "build_pending_approvals",
    "SUG_KEEP_RUNNING",
    "SUG_WATCH_ONLY",
    "SUG_REDUCE_SIZE",
    "SUG_TIGHTEN_FILTER",
    "SUG_DISABLE_SESSION",
    "SUG_REVIEW_PARAMETERS",
    "SUG_PAUSE_STRATEGY",
    "SUG_REVALIDATE_BACKTEST",
    # Review report (read-only summary)
    "ReviewFinding",
    "ReviewSection",
    "ReviewReport",
    "build_review_report",
    "build_review_report_all_windows",
    # Review actions queue (manual-only queue)
    "ReviewAction",
    "ReviewActionsQueue",
    "build_review_actions_queue",
    "build_review_actions_queue_all_windows",
]