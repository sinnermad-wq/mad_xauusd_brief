"""Strategy Health Monitor + Suggestion Engine v1.

Public entry points::

    from src.strategy_health import load_latest_snapshot, load_snapshot_from_paths

    snap = load_latest_snapshot()
    snap = load_snapshot_from_paths(
        fusion_history_path="data/fusion_history/2026-07-10.json",
        backtest_report_path="data/backtest/2026-07-10_backtest.json",
    )
    print(snap.health_status, snap.suggestions)

Data structures::

    from src.strategy_health.models import (
        StrategyHealthSnapshot, StrategyDiagnostic, StrategySuggestion,
        StrategyApprovalState, StrategyHealthConfig,
        HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED, HEALTH_UNKNOWN,
        SEVERITY_OK, SEVERITY_WARN, SEVERITY_CRITICAL, SEVERITY_UNKNOWN,
        APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_SUPERSEDED,
    )

Approval store (manual-edit only)::

    from src.strategy_health.approval import (
        load_approvals, resolve_approval, get_pending, upsert_approval,
    )
"""
from .models import (
    StrategyHealthConfig,
    StrategyHealthSnapshot,
    StrategyDiagnostic,
    StrategySuggestion,
    StrategyApprovalState,
    HEALTH_GREEN,
    HEALTH_YELLOW,
    HEALTH_RED,
    HEALTH_UNKNOWN,
    SEVERITY_OK,
    SEVERITY_WARN,
    SEVERITY_CRITICAL,
    SEVERITY_UNKNOWN,
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_SUPERSEDED,
    VALID_APPROVAL_STATUSES,
    SUG_KEEP_RUNNING,
    SUG_WATCH_ONLY,
    SUG_REDUCE_SIZE,
    SUG_TIGHTEN_FILTER,
    SUG_DISABLE_SESSION,
    SUG_REVIEW_PARAMETERS,
    SUG_PAUSE_STRATEGY,
    SUG_REVALIDATE_BACKTEST,
)
from .snapshot_loader import load_latest_snapshot, load_snapshot_from_paths
from .snapshot_builder import build_health_snapshot
from .suggestion import compute_suggestions, build_pending_approvals
from .approval import (
    load_approvals,
    upsert_approval,
    resolve_approval,
    get_pending,
    diff_approvals,
)

__all__ = [
    # Entry points
    "load_latest_snapshot",
    "load_snapshot_from_paths",
    "build_health_snapshot",
    "compute_suggestions",
    # Data models
    "StrategyHealthConfig",
    "StrategyHealthSnapshot",
    "StrategyDiagnostic",
    "StrategySuggestion",
    "StrategyApprovalState",
    # Health statuses
    "HEALTH_GREEN",
    "HEALTH_YELLOW",
    "HEALTH_RED",
    "HEALTH_UNKNOWN",
    # Severities
    "SEVERITY_OK",
    "SEVERITY_WARN",
    "SEVERITY_CRITICAL",
    "SEVERITY_UNKNOWN",
    # Approval statuses
    "APPROVAL_PENDING",
    "APPROVAL_APPROVED",
    "APPROVAL_REJECTED",
    "APPROVAL_SUPERSEDED",
    "VALID_APPROVAL_STATUSES",
    # Suggestion kinds
    "SUG_KEEP_RUNNING",
    "SUG_WATCH_ONLY",
    "SUG_REDUCE_SIZE",
    "SUG_TIGHTEN_FILTER",
    "SUG_DISABLE_SESSION",
    "SUG_REVIEW_PARAMETERS",
    "SUG_PAUSE_STRATEGY",
    "SUG_REVALIDATE_BACKTEST",
    # Approval store
    "load_approvals",
    "upsert_approval",
    "resolve_approval",
    "get_pending",
    "diff_approvals",
    "build_pending_approvals",
]