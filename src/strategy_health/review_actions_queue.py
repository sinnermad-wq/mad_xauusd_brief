"""Review Actions Queue — read-only prioritized action list (manual review only).

Aggregates pending approvals + repeated suggestions + repeated diagnostics +
top deteriorations into a single sorted queue for human review.

Reuses existing history/trend APIs. Does not mutate any state.

Guardrails (still apply):
- manual-only / read-only
- no auto-apply
- no execution / no trading
- no live mutation (does not write approvals.jsonl, history.jsonl, etc.)
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .history import compute_all_trend_windows
from .history_models import (
    ApprovalTrend,
    HealthTrendMetrics,
    TrendDirection,
)
from .models import (
    APPROVAL_PENDING,
    SEVERITY_CRITICAL,
    SEVERITY_WARN,
    StrategyHealthSnapshot,
)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ReviewAction:
    """One prioritized action item in the review queue."""

    # Identity
    action_id: str            # stable id, e.g. "approval:<sid>", "diag:<name>", "sug:<type>"
    category: str             # "approval" | "suggestion" | "diagnostic" | "deterioration"

    # Display
    title: str                # short, 1 line, human readable
    detail: str = ""          # longer annotation / metric snapshot
    severity: str = "info"    # "critical" | "warn" | "info"

    # Sorting signposts (all optional — defaults are gentle)
    priority_score: float = 0.0   # higher = more urgent
    age_days: float = 0.0         # approximate age in days
    frequency: int = 1            # occurrences in window (>=1)
    trend: str = "stable"         # "improving" | "stable" | "degrading" | "unknown"


@dataclass
class ReviewActionsQueue:
    """A prioritized review queue for one window."""

    window_days: int
    actions: List[ReviewAction] = field(default_factory=list)
    has_sufficient_history: bool = False
    empty_reason: str = ""
    generated_at_iso: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )

    def is_empty(self) -> bool:
        return len(self.actions) == 0

    def filter_by_category(self, category: str) -> List[ReviewAction]:
        return [a for a in self.actions if a.category == category]

    def to_table(self) -> Optional[List[tuple]]:
        """Render as a list-of-tuples for st.table or pandas rendering."""
        if self.is_empty():
            return None
        header = ("#", "Category", "Severity", "Title", "Priority", "Freq", "Trend")
        rows: List[tuple] = []
        for i, a in enumerate(self.actions, start=1):
            rows.append((
                i,
                a.category,
                a.severity,
                a.title,
                f"{a.priority_score:.1f}",
                a.frequency,
                a.trend,
            ))
        return [header] + rows

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append(f"# Review Actions Queue ({self.window_days}d)")
        lines.append("")
        lines.append(
            f"_Generated: {self.generated_at_iso} | "
            f"Total actions: **{len(self.actions)}** | "
            f"Sufficient history: **{self.has_sufficient_history}**_"
        )

        if self.is_empty():
            lines.append("")
            lines.append("✅ **Queue is empty** — nothing to review.")
            if self.empty_reason:
                lines.append(f"_{self.empty_reason}_")
            return "\n".join(lines)

        lines.append("")
        # Group by category for readability
        by_cat: dict[str, List[ReviewAction]] = {}
        for a in self.actions:
            by_cat.setdefault(a.category, []).append(a)

        cat_order = ("approval", "deterioration", "diagnostic", "suggestion")
        cat_label = {
            "approval": "📋 Pending Approvals",
            "deterioration": "📉 Top Deteriorations",
            "diagnostic": "⚠️ Repeated Diagnostics",
            "suggestion": "🔁 Repeated Suggestions",
        }
        for cat in cat_order:
            actions = by_cat.get(cat, [])
            if not actions:
                continue
            lines.append(f"## {cat_label[cat]} ({len(actions)})")
            lines.append("")
            for i, a in enumerate(actions, start=1):
                sev_icon = {
                    "critical": "🔴",
                    "warn": "🟡",
                    "info": "ℹ️",
                }.get(a.severity, "•")
                lines.append(
                    f"{i}. {sev_icon} **{a.title}** "
                    f"(priority {a.priority_score:.1f}, {a.frequency}×, {a.trend})"
                )
                if a.detail:
                    lines.append(f"   - _{a.detail}_")
            lines.append("")
        return "\n".join(lines)

    def to_csv(self) -> str:
        """Render as CSV string. Empty queue returns header-only."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["window_days", "priority", "category", "severity",
             "title", "detail", "frequency", "age_days", "trend"]
        )
        for a in self.actions:
            writer.writerow([
                self.window_days,
                f"{a.priority_score:.2f}",
                a.category,
                a.severity,
                a.title,
                a.detail,
                a.frequency,
                f"{a.age_days:.2f}",
                a.trend,
            ])
        return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Priority scoring
#
# score = severity_weight + trend_weight + frequency_weight + age_weight
# Higher = more urgent. Capped / bounded to keep comparable across windows.
# ─────────────────────────────────────────────────────────────────────────────


def _severity_weight(sev: str) -> float:
    return {"critical": 50.0, "warn": 25.0, "info": 5.0}.get(sev, 0.0)


def _trend_weight(trend: str | TrendDirection) -> float:
    if isinstance(trend, TrendDirection):
        trend = trend.value
    return {
        "degrading": 20.0,
        "improving": -5.0,    # improving = less urgent
        "stable": 0.0,
        "unknown": 0.0,
        "insufficient_data": 0.0,
    }.get(trend, 0.0)


def _frequency_weight(freq: int, window_days: int) -> float:
    """Higher frequency in the window yields higher weight (capped)."""
    if window_days <= 0:
        return 0.0
    rate = freq / max(1, window_days)
    return min(rate * 10.0, 20.0)


def _age_weight(age_days: float) -> float:
    """Older items are more urgent. Capped at 14 days."""
    return min(max(age_days, 0.0) * 1.0, 14.0)


def _score(
    severity: str,
    trend: str | TrendDirection,
    frequency: int,
    age_days: float,
    window_days: int,
) -> float:
    return (
        _severity_weight(severity)
        + _trend_weight(trend)
        + _frequency_weight(frequency, window_days)
        + _age_weight(age_days)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Builders — extract ReviewActions from snapshot + history
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days_from(updated_at: str) -> float:
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - ts
        return max(delta.total_seconds() / 86400.0, 0.0)
    except Exception:
        return 0.0


def _approvals_actions(
    snap: StrategyHealthSnapshot, window_days: int
) -> List[ReviewAction]:
    out: List[ReviewAction] = []
    pending = [a for a in (snap.pending_approvals or ()) if a.status == APPROVAL_PENDING]
    for rec in pending:
        age = _age_days_from(rec.updated_at or rec.created_at or _now_iso())
        sev = "warn" if age < 7 else "critical"
        score = _score(sev, "stable", 1, age, window_days)
        out.append(ReviewAction(
            action_id=f"approval:{rec.suggestion_id}",
            category="approval",
            title=f"Pending approval: {rec.kind}",
            detail=(
                f"id={rec.suggestion_id} | status={rec.status} | "
                f"age={age:.1f}d"
            ),
            severity=sev,
            priority_score=score,
            age_days=age,
            frequency=1,
            trend="stable",
        ))
    return out


def _deterioration_actions(
    snap: StrategyHealthSnapshot, trend: HealthTrendMetrics, window_days: int
) -> List[ReviewAction]:
    out: List[ReviewAction] = []
    for dt in trend.diagnostic_trends:
        # Only interested in things that look like they need attention
        trend_str = (
            dt.metric_trend.value
            if isinstance(dt.metric_trend, TrendDirection)
            else str(dt.metric_trend)
        )
        if dt.critical_days > 0:
            sev = "critical"
        elif dt.warn_days > 0 and trend_str in ("degrading", "stable"):
            sev = "warn"
        else:
            continue
        freq = max(dt.critical_days, dt.warn_days, 1)
        score = _score(sev, trend_str, freq, 0.0, window_days) + freq * 2
        title = (
            f"{dt.diagnostic_name}: critical {dt.critical_days}d/"
            f"warn {dt.warn_days}d"
        )
        detail = (
            f"trend={trend_str} | dominant={dt.dominant_severity} | "
            f"persistence warn={dt.warn_persistence_pct:.1f}%/"
            f"crit={dt.critical_persistence_pct:.1f}%"
        )
        out.append(ReviewAction(
            action_id=f"deterioration:{dt.diagnostic_name}",
            category="deterioration",
            title=title,
            detail=detail,
            severity=sev,
            priority_score=score,
            trend=trend_str,
            frequency=freq,
        ))
    return out


def _repeated_diagnostic_actions(
    snap: StrategyHealthSnapshot, trend: HealthTrendMetrics, window_days: int
) -> List[ReviewAction]:
    out: List[ReviewAction] = []
    for dt in trend.diagnostic_trends:
        # Repeated means persistence > 50% (warn or crit)
        if dt.critical_days == 0 and dt.warn_days == 0:
            continue
        if dt.critical_days + dt.warn_days == 0:
            continue
        persistence = (
            dt.warn_persistence_pct if dt.warn_persistence_pct >= 0 else 0.0
        )
        if persistence < 50.0 and dt.critical_days == 0:
            continue
        sev = "critical" if dt.critical_days > 0 else "warn"
        trend_str = (
            dt.metric_trend.value
            if isinstance(dt.metric_trend, TrendDirection)
            else str(dt.metric_trend)
        )
        score = _score(sev, trend_str, dt.warn_days + dt.critical_days, 0.0, window_days)
        out.append(ReviewAction(
            action_id=f"diagnostic:{dt.diagnostic_name}",
            category="diagnostic",
            title=f"Repeated {dt.diagnostic_name} ({persistence:.0f}% of window)",
            detail=(
                f"warn_days={dt.warn_days} | crit_days={dt.critical_days} "
                f"| trend={trend_str}"
            ),
            severity=sev,
            priority_score=score,
            trend=trend_str,
            frequency=dt.warn_days + dt.critical_days,
        ))
    return out


def _repeated_suggestion_actions(
    snap: StrategyHealthSnapshot, trend: HealthTrendMetrics, window_days: int
) -> List[ReviewAction]:
    out: List[ReviewAction] = []
    for st in trend.suggestion_trends:
        if st.suggestion_type == "any":
            continue
        if not st.is_recurring or st.entries_with_this_suggestion <= 1:
            continue
        trend_str = (
            st.trend.value if isinstance(st.trend, TrendDirection) else str(st.trend)
        )
        sev = "warn" if st.entries_with_this_suggestion >= 3 else "info"
        score = _score(sev, trend_str, st.occurrences, 0.0, window_days)
        out.append(ReviewAction(
            action_id=f"suggestion:{st.suggestion_type}",
            category="suggestion",
            title=f"Recurring suggestion: {st.suggestion_type}",
            detail=(
                f"occurrences={st.occurrences} | "
                f"entries={st.entries_with_this_suggestion}/{st.total_entries} | "
                f"trend={trend_str}"
            ),
            severity=sev,
            priority_score=score,
            trend=trend_str,
            frequency=st.entries_with_this_suggestion,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────


def build_review_actions_queue(
    snap: StrategyHealthSnapshot, window_days: int = 7
) -> ReviewActionsQueue:
    """Build a prioritized ReviewActionsQueue for one window.

    Args:
        snap: current StrategyHealthSnapshot.
        window_days: 7 / 14 / 30. Defaults to 7.

    Returns:
        ReviewActionsQueue with actions sorted by priority_score (desc).
        Empty queue with reason when nothing actionable exists.
    """
    windows = compute_all_trend_windows()
    trend = windows.get(f"{window_days}d")
    if trend is None:
        trend = HealthTrendMetrics(
            window_days=window_days,
            computed_at_iso=_now_iso(),
            entries_used=0,
        )

    has_history = trend.entries_used >= 2

    actions: List[ReviewAction] = []
    actions.extend(_approvals_actions(snap, window_days))

    if has_history:
        actions.extend(_deterioration_actions(snap, trend, window_days))
        actions.extend(_repeated_diagnostic_actions(snap, trend, window_days))
        actions.extend(_repeated_suggestion_actions(snap, trend, window_days))
    else:
        # Fallback: surface latest snapshot diagnostics at warn/crit
        for d in snap.diagnostics:
            if d.severity == SEVERITY_CRITICAL:
                sev = "critical"
            elif d.severity == SEVERITY_WARN:
                sev = "warn"
            else:
                continue
            actions.append(ReviewAction(
                action_id=f"diagnostic:{d.name}",
                category="diagnostic",
                title=f"Live diagnostic at {d.severity}: {d.name}",
                detail=d.summary or "",
                severity=sev,
                priority_score=_score(sev, "unknown", 1, 0.0, window_days),
                frequency=1,
                trend="unknown",
            ))

    # Stable priority sort: primary score desc, severity, age desc, then id
    sev_rank = {"critical": 3, "warn": 2, "info": 1}
    actions.sort(
        key=lambda a: (
            -a.priority_score,
            -sev_rank.get(a.severity, 0),
            -a.age_days,
            a.action_id,
        )
    )

    empty_reason = ""
    if not actions:
        if not has_history:
            empty_reason = "No pending approvals + insufficient history (< 2 entries) — nothing to review."
        else:
            empty_reason = "No pending approvals and all diagnostics/suggestions look stable in this window."

    return ReviewActionsQueue(
        window_days=window_days,
        actions=actions,
        has_sufficient_history=has_history,
        empty_reason=empty_reason,
    )


def build_review_actions_queue_all_windows(
    snap: StrategyHealthSnapshot,
) -> dict:
    """Build review action queues for all 3 windows (7d / 14d / 30d).

    Returns dict like {"7d": ReviewActionsQueue, "14d": ..., "30d": ...}.
    """
    return {f"{d}d": build_review_actions_queue(snap, d) for d in (7, 14, 30)}
