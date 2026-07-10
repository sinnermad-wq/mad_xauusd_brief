"""History + trend data structures for Strategy Health Monitor v1."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TrendDirection(str, Enum):
    """Direction of a trend metric over a time window."""
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    UNKNOWN = "unknown"
    INSUFFICIENT_DATA = "insufficient_data"


# ─────────────────────────────────────────────────────────────────────────────
# History Record (what gets stored)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HealthHistoryEntry:
    """One point-in-time record appended to history.

    Stored as a single JSON line in data/strategy_health/history.jsonl.
    Compact — derived from StrategyHealthSnapshot but stored separately
    to keep history independent and corruption-contained.
    """
    timestamp_iso: str          # ISO8601 UTC, e.g. "2026-07-10T09:00:00Z"
    health_status: str          # "green" / "yellow" / "red" / "unknown"
    diagnostic_count: int       # number of diagnostics run
    warn_count: int             # diagnostics with severity == warn
    critical_count: int         # diagnostics with severity == critical
    unknown_count: int           # diagnostics with severity == unknown
    suggestion_count: int       # number of pending suggestions
    dominant_regime: str        # most common bias_regime, or "unknown"
    regime_mix_shift_pct: float # from regime diagnostic, or -1.0 if missing
    avg_hit_rate_pct: float      # from performance diagnostic, or -1.0
    max_drawdown_pct: float      # from drawdown diagnostic, or -1.0
    cost_drag_pct: float        # from cost diagnostic, or -1.0
    oldest_data_age_minutes: float  # from freshness: largest file age, or -1.0
    pending_approvals: int       # current pending approvals count
    approved_approvals: int      # total approved (all time)
    rejected_approvals: int      # total rejected (all time)
    snapshot_version: str = "v1"  # schema version for forward compat

    def to_dict(self) -> dict:
        return {
            "timestamp_iso": self.timestamp_iso,
            "health_status": self.health_status,
            "diagnostic_count": self.diagnostic_count,
            "warn_count": self.warn_count,
            "critical_count": self.critical_count,
            "unknown_count": self.unknown_count,
            "suggestion_count": self.suggestion_count,
            "dominant_regime": self.dominant_regime,
            "regime_mix_shift_pct": self.regime_mix_shift_pct,
            "avg_hit_rate_pct": self.avg_hit_rate_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "cost_drag_pct": self.cost_drag_pct,
            "oldest_data_age_minutes": self.oldest_data_age_minutes,
            "pending_approvals": self.pending_approvals,
            "approved_approvals": self.approved_approvals,
            "rejected_approvals": self.rejected_approvals,
            "snapshot_version": self.snapshot_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HealthHistoryEntry:
        return cls(
            timestamp_iso=d["timestamp_iso"],
            health_status=d["health_status"],
            diagnostic_count=d["diagnostic_count"],
            warn_count=d["warn_count"],
            critical_count=d["critical_count"],
            unknown_count=d["unknown_count"],
            suggestion_count=d["suggestion_count"],
            dominant_regime=d["dominant_regime"],
            regime_mix_shift_pct=float(d["regime_mix_shift_pct"]),
            avg_hit_rate_pct=float(d["avg_hit_rate_pct"]),
            max_drawdown_pct=float(d["max_drawdown_pct"]),
            cost_drag_pct=float(d["cost_drag_pct"]),
            oldest_data_age_minutes=float(d["oldest_data_age_minutes"]),
            pending_approvals=int(d["pending_approvals"]),
            approved_approvals=int(d["approved_approvals"]),
            rejected_approvals=int(d["rejected_approvals"]),
            snapshot_version=d.get("snapshot_version", "v1"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Trend Summary (computed on demand)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DiagnosticTrend:
    """Trend for a single diagnostic category over a time window."""
    diagnostic_name: str
    window_days: int

    # Count of days with each severity (derived from entries in window)
    warn_days: int = 0
    critical_days: int = 0
    ok_days: int = 0
    unknown_days: int = 0
    total_entries: int = 0

    # Persistence rates
    warn_persistence_pct: float = -1.0   # warn_days / total_entries * 100
    critical_persistence_pct: float = -1.0  # critical_days / total_entries * 100

    # Trend direction for the underlying metric (e.g., hit_rate, drawdown)
    metric_trend: TrendDirection = TrendDirection.UNKNOWN
    metric_trend_strength: float = -1.0  # 0-1, magnitude of change relative to baseline

    # Most common severity in window
    dominant_severity: str = "unknown"

    @property
    def is_stable(self) -> bool:
        return self.dominant_severity == "ok" and self.critical_days == 0

    @property
    def is_healthy(self) -> bool:
        return self.critical_days == 0 and self.warn_days == 0


@dataclass(frozen=True)
class SuggestionTrend:
    """Trend for suggestion frequency over a time window."""
    suggestion_type: str
    window_days: int

    occurrences: int = 0
    appearances_per_day: float = -1.0
    trend: TrendDirection = TrendDirection.UNKNOWN

    # Was it recurring (appeared in multiple entries)?
    is_recurring: bool = False
    entries_with_this_suggestion: int = 0


@dataclass(frozen=True)
class ApprovalTrend:
    """Trend for approval distribution."""
    window_days: int

    total_entries: int = 0
    entries_with_pending: int = 0
    entries_with_approved: int = 0
    entries_with_rejected: int = 0

    pending_rate_pct: float = -1.0  # entries with pending / total * 100
    approved_rate_pct: float = -1.0
    rejected_rate_pct: float = -1.0

    # Approval backlog
    current_pending: int = -1
    current_approved: int = -1
    current_rejected: int = -1


@dataclass(frozen=True)
class RegimeTrend:
    """Trend for regime/session distribution over a time window."""
    window_days: int

    total_entries: int = 0

    # Regime mix shift trend
    avg_mix_shift_pct: float = -1.0
    max_mix_shift_pct: float = -1.0
    mix_shift_trend: TrendDirection = TrendDirection.UNKNOWN

    # Most common regime
    dominant_regime: str = "unknown"
    regime_distribution: dict = field(default_factory=dict)

    # Regime stability (how often did dominant regime stay the same?)
    regime_stability_pct: float = -1.0

    # Session drift: any session appearing/disappearing?
    session_set: tuple = field(default_factory=tuple)
    new_sessions: tuple = field(default_factory=tuple)  # appeared in recent half, not prior


@dataclass(frozen=True)
class HealthTrendMetrics:
    """Full trend summary for a time window.

    Computed on demand from history entries.
    """
    window_days: int
    computed_at_iso: str
    entries_used: int

    # Health status summary
    green_days: int = 0
    yellow_days: int = 0
    red_days: int = 0
    unknown_days: int = 0
    health_status_trend: TrendDirection = TrendDirection.UNKNOWN

    # Per-diagnostic trends
    diagnostic_trends: tuple = field(default_factory=tuple)

    # Suggestion trends (top N by frequency)
    suggestion_trends: tuple = field(default_factory=tuple)

    # Approval trend
    approval_trend: Optional[ApprovalTrend] = None

    # Regime/session trend
    regime_trend: Optional[RegimeTrend] = None

    # Health score (0-100, weighted composite)
    health_score: float = -1.0

    # Summary text
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "computed_at_iso": self.computed_at_iso,
            "entries_used": self.entries_used,
            "green_days": self.green_days,
            "yellow_days": self.yellow_days,
            "red_days": self.red_days,
            "unknown_days": self.unknown_days,
            "health_status_trend": self.health_status_trend.value,
            "health_score": self.health_score,
            "summary": self.summary,
        }