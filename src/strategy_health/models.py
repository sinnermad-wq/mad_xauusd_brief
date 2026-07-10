"""Strategy Health v1 data structures.

Pure value-types — frozen dataclasses, JSON-serializable.
No I/O. No engine logic.

Severity scale:
  ok | warn | critical | unknown

Suggestion kinds:
  keep_running | watch_only | reduce_size | tighten_filter
  disable_session | review_parameters | pause_strategy
  | revalidate_backtest
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


HEALTH_GREEN = "green"
HEALTH_YELLOW = "yellow"
HEALTH_RED = "red"
HEALTH_UNKNOWN = "unknown"
VALID_HEALTH_STATUSES = (
    HEALTH_GREEN,
    HEALTH_YELLOW,
    HEALTH_RED,
    HEALTH_UNKNOWN,
)

SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_CRITICAL = "critical"
SEVERITY_UNKNOWN = "unknown"
VALID_SEVERITIES = (
    SEVERITY_OK,
    SEVERITY_WARN,
    SEVERITY_CRITICAL,
    SEVERITY_UNKNOWN,
)

SUG_KEEP_RUNNING = "keep_running"
SUG_WATCH_ONLY = "watch_only"
SUG_REDUCE_SIZE = "reduce_size"
SUG_TIGHTEN_FILTER = "tighten_filter"
SUG_DISABLE_SESSION = "disable_session"
SUG_REVIEW_PARAMETERS = "review_parameters"
SUG_PAUSE_STRATEGY = "pause_strategy"
SUG_REVALIDATE_BACKTEST = "revalidate_backtest"
VALID_SUGGESTIONS = (
    SUG_KEEP_RUNNING,
    SUG_WATCH_ONLY,
    SUG_REDUCE_SIZE,
    SUG_TIGHTEN_FILTER,
    SUG_DISABLE_SESSION,
    SUG_REVIEW_PARAMETERS,
    SUG_PAUSE_STRATEGY,
    SUG_REVALIDATE_BACKTEST,
)

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_SUPERSEDED = "superseded"
VALID_APPROVAL_STATUSES = (
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_SUPERSEDED,
)

SOURCE_BACKTEST = "backtest"
SOURCE_FUSION_HISTORY = "fusion_history"
SOURCE_CANDLESTICK = "candlestick"
SOURCE_FILE_MTIME = "file_mtime"


@dataclass(frozen=True)
class StrategyHealthConfig:
    """Tunable thresholds. JSON-loadable. Manual edit only."""
    performance_window: int = 20
    regime_window: int = 30
    cost_window: int = 50
    signal_window: int = 50
    drawdown_window_days: int = 90
    fusion_history_ttl_minutes: int = 90
    backtest_report_ttl_minutes: int = 240
    candlestick_snapshot_ttl_minutes: int = 90
    cost_drag_warn_pct: float = 10.0
    cost_drag_critical_pct: float = 30.0
    drawdown_warn_pct: float = 7.5
    drawdown_critical_pct: float = 15.0
    min_session_sample: int = 10
    session_hit_rate_disable_threshold: float = 35.0
    low_conf_drift_ratio_warn: float = 0.65
    regime_mix_shift_warn_pct: float = 40.0
    expected_baseline_hit_rate: float = 55.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyHealthConfig":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class StrategyDiagnostic:
    """One diagnostic row: a measure + severity + explanation."""
    name: str
    severity: str
    summary: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    source: str = ""

    def is_critical(self) -> bool:
        return self.severity == SEVERITY_CRITICAL

    def is_unknown(self) -> bool:
        return self.severity == SEVERITY_UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "severity": self.severity,
            "summary": self.summary,
            "metrics": dict(self.metrics),
            "reasons": list(self.reasons),
            "source": self.source,
        }


@dataclass(frozen=True)
class StrategySuggestion:
    """One actionable, human-resolved suggestion."""
    suggestion_id: str
    kind: str
    priority: int
    title: str
    rationale: str
    diagnostic_name: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    actions: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "kind": self.kind,
            "priority": self.priority,
            "title": self.title,
            "rationale": self.rationale,
            "diagnostic_name": self.diagnostic_name,
            "metrics": dict(self.metrics),
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class StrategyApprovalState:
    """One persisted approval record (manual edit by user)."""
    suggestion_id: str
    kind: str
    status: str
    created_at: str
    updated_at: str
    note: str = ""
    resolved_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "note": self.note,
            "resolved_by": self.resolved_by,
        }

    def is_pending(self) -> bool:
        return self.status == APPROVAL_PENDING


@dataclass(frozen=True)
class StrategyHealthSnapshot:
    """Top-level container: verdict + diagnostics + suggestions."""
    snapshot_id: str
    generated_at: str
    health_status: str
    diagnostics: Tuple[StrategyDiagnostic, ...]
    suggestions: Tuple[StrategySuggestion, ...]
    pending_approvals: Tuple[StrategyApprovalState, ...]
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    source_inventory: Dict[str, bool] = field(default_factory=dict)

    def status_for(self, name: str) -> Optional[str]:
        for d in self.diagnostics:
            if d.name == name:
                return d.severity
        return None

    def diagnostics_by_severity(self, severity: str) -> List[StrategyDiagnostic]:
        return [d for d in self.diagnostics if d.severity == severity]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at,
            "health_status": self.health_status,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "suggestions": [s.to_dict() for s in self.suggestions],
            "pending_approvals": [a.to_dict() for a in self.pending_approvals],
            "config_snapshot": dict(self.config_snapshot),
            "warnings": list(self.warnings),
            "source_inventory": dict(self.source_inventory),
        }

