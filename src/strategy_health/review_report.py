"""Dashboard Review Report — read-only summary aggregating snapshot + history.

Reuses existing history/trend APIs to produce a human-reviewable summary
without altering health status logic, applying suggestions, or executing trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .history import compute_all_trend_windows
from .history_models import (
    HealthTrendMetrics,
    TrendDirection,
)
from .models import (
    HEALTH_GREEN,
    HEALTH_RED,
    HEALTH_UNKNOWN,
    HEALTH_YELLOW,
    StrategyHealthSnapshot,
)


# ─────────────────────────────────────────────────────────────────────────────
# Section: Section data classes (lightweight, plain strings for dashboard)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ReviewFinding:
    """One bullet-point observation in the review report."""
    severity: str   # "ok" | "warn" | "critical" | "info"
    text: str

    def display(self) -> str:
        icon = {
            "ok": "🟢",
            "warn": "🟡",
            "critical": "🔴",
            "info": "ℹ️",
        }.get(self.severity, "•")
        return f"{icon} {self.text}"


@dataclass
class ReviewSection:
    """One section of the review (Executive Summary, Deteriorations, etc.)."""
    heading: str
    findings: List[ReviewFinding] = field(default_factory=list)
    table: Optional[List[tuple]] = None  # list of row-tuples for pandas/df rendering

    def has_content(self) -> bool:
        return bool(self.findings) or bool(self.table)


@dataclass
class ReviewReport:
    """Full report wrapper — easy to convert to markdown."""
    window_days: int
    sections: List[ReviewSection] = field(default_factory=list)
    has_sufficient_history: bool = False
    health_score: float = -1.0
    health_status: str = HEALTH_UNKNOWN
    health_status_trend: str = "unknown"
    generated_at_iso: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append(f"# Strategy Health Review ({self.window_days}d)")
        lines.append("")
        lines.append(
            f"_Generated: {self.generated_at_iso} | "
            f"Health: **{self.health_status.upper()}** "
            f"({self.health_status_trend}) "
            f"| Score: **{self.health_score:.1f}** / 100"
        )

        if not self.has_sufficient_history:
            lines.append("")
            lines.append(
                "⚠️ Insufficient history (< 2 entries). "
                "Showing current-snapshot summary only."
            )

        lines.append("")
        for sec in self.sections:
            if not sec.has_content():
                continue
            lines.append(f"## {sec.heading}")
            lines.append("")
            for f in sec.findings:
                lines.append(f"- {f.display()}")
            if sec.table:
                # Markdown table: convert list-of-tuples into pipe rows
                header = sec.table[0]
                rows = sec.table[1:]
                lines.append("")
                lines.append("| " + " | ".join(str(c) for c in header) + " |")
                lines.append("| " + " | ".join("---" for _ in header) + " |")
                for r in rows:
                    lines.append("| " + " | ".join(str(c) for c in r) + " |")
                lines.append("")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — derive small facts from snapshot + trends
# ─────────────────────────────────────────────────────────────────────────────


_STATUS_ICON = {
    HEALTH_GREEN: "🟢",
    HEALTH_YELLOW: "🟡",
    HEALTH_RED: "🔴",
    HEALTH_UNKNOWN: "⚪",
}


def _icon(name: str) -> str:
    return _STATUS_ICON.get(name, "⚪")


def _direction_arrow(direction: str | TrendDirection) -> str:
    if isinstance(direction, TrendDirection):
        direction = direction.value
    return {
        "improving": "↗️",
        "degrading": "↘️",
        "stable": "→",
        "unknown": "?",
        "insufficient_data": "…",
    }.get(direction, "?")


# ─────────────────────────────────────────────────────────────────────────────
# Section builders — each takes the snapshot + trend window, returns section
# ─────────────────────────────────────────────────────────────────────────────


def _exec_summary(snap: StrategyHealthSnapshot, t: HealthTrendMetrics) -> ReviewSection:
    findings: List[ReviewFinding] = []

    icon = _icon(snap.health_status)
    findings.append(ReviewFinding(
        "info",
        f"{icon} Current health status: **{snap.health_status.upper()}**",
    ))

    arrow = _direction_arrow(t.health_status_trend)
    trend_label = (
        t.health_status_trend.value
        if isinstance(t.health_status_trend, TrendDirection)
        else str(t.health_status_trend)
    )
    findings.append(ReviewFinding(
        "info",
        f"Status trend over {t.window_days}d window: **{trend_label}** {arrow}",
    ))

    findings.append(ReviewFinding(
        "info",
        f"Composite health score: **{t.health_score:.1f}** / 100",
    ))

    findings.append(ReviewFinding(
        "info",
        f"Entries used: {t.entries_used} (green={t.green_days}, "
        f"yellow={t.yellow_days}, red={t.red_days}, unknown={t.unknown_days})",
    ))

    # Snapshot diagnostics summary
    n_diags = len(snap.diagnostics)
    n_warn = sum(1 for d in snap.diagnostics if d.severity == "warn")
    n_crit = sum(1 for d in snap.diagnostics if d.severity == "critical")
    findings.append(ReviewFinding(
        "info",
        f"Latest diagnostics: {n_diags} total "
        f"({n_warn} warn, {n_crit} critical)",
    ))

    if n_crit > 0:
        findings.append(ReviewFinding(
            "critical",
            f"{n_crit} diagnostic(s) at CRITICAL severity — review immediately.",
        ))
    elif n_warn > 0:
        findings.append(ReviewFinding(
            "warn",
            f"{n_warn} diagnostic(s) at WARN severity — monitor.",
        ))
    else:
        findings.append(ReviewFinding(
            "ok",
            "All diagnostics at OK — no immediate concerns.",
        ))

    return ReviewSection(heading="1. Executive Summary", findings=findings)


def _key_deteriorations(snap: StrategyHealthSnapshot, t: HealthTrendMetrics) -> ReviewSection:
    findings: List[ReviewFinding] = []
    table_rows: List[tuple] = [("Diagnostic", "Severity", "Trend", "Status")]

    for dt in t.diagnostic_trends:
        arrow = _direction_arrow(dt.metric_trend)
        trend_label = (
            dt.metric_trend.value
            if isinstance(dt.metric_trend, TrendDirection)
            else str(dt.metric_trend)
        )
        # Map dominant severity
        if dt.critical_days > 0:
            severity = "crit"
            icon = "🔴"
            finding_sev = "critical"
            message = f"{icon} {dt.diagnostic_name}: critical in {dt.critical_days}/{dt.total_entries} days"
            findings.append(ReviewFinding(finding_sev, message))
        elif dt.warn_days > 0:
            severity = "warn"
            icon = "🟡"
            finding_sev = "warn"
            message = f"{icon} {dt.diagnostic_name}: warn in {dt.warn_days}/{dt.total_entries} days"
            findings.append(ReviewFinding(finding_sev, message))
        else:
            severity = "ok"
            icon = "🟢"

        table_rows.append((dt.diagnostic_name, icon, f"{arrow} {trend_label}", dt.dominant_severity))

    return ReviewSection(
        heading="2. Key Deteriorations",
        findings=findings,
        table=table_rows if len(table_rows) > 1 else None,
    )


def _repeated_diagnostics(snap: StrategyHealthSnapshot, t: HealthTrendMetrics) -> ReviewSection:
    findings: List[ReviewFinding] = []
    table_rows: List[tuple] = [("Diagnostic", "Warn Days", "Crit Days", "Persistence (warn / crit %)")]

    for dt in t.diagnostic_trends:
        # A diagnostic is "repeated" if it had any warn or critical days in the window
        if dt.warn_days > 0 or dt.critical_days > 0:
            sev = "critical" if dt.critical_days > 0 else "warn"
            icon = "🔴" if dt.critical_days > 0 else "🟡"
            findings.append(ReviewFinding(
                sev,
                f"{icon} {dt.diagnostic_name}: warn {dt.warn_days}d, "
                f"critical {dt.critical_days}d (window {dt.window_days}d)",
            ))
            table_rows.append((
                dt.diagnostic_name,
                str(dt.warn_days),
                str(dt.critical_days),
                f"{dt.warn_persistence_pct:.1f}% / {dt.critical_persistence_pct:.1f}%",
            ))
        elif dt.dominant_severity == "ok":
            findings.append(ReviewFinding(
                "ok",
                f"🟢 {dt.diagnostic_name}: stable (no warn/critical days)",
            ))

    return ReviewSection(
        heading="3. Repeated Diagnostics / Warnings",
        findings=findings,
        table=table_rows if len(table_rows) > 1 else None,
    )


def _session_regime(snap: StrategyHealthSnapshot, t: HealthTrendMetrics) -> ReviewSection:
    findings: List[ReviewFinding] = []
    rt = t.regime_trend

    if rt is None or rt.total_entries == 0:
        return ReviewSection(heading="4. Session / Regime Review", findings=[
            ReviewFinding("info", "No regime/session data in window."),
        ])

    findings.append(ReviewFinding(
        "info",
        f"Dominant regime: **{rt.dominant_regime}**",
    ))

    if rt.mix_shift_trend != TrendDirection.UNKNOWN:
        arrow = _direction_arrow(rt.mix_shift_trend)
        findings.append(ReviewFinding(
            "info",
            f"Regime mix shift trend: {arrow} "
            f"(avg {rt.avg_mix_shift_pct:.2f}% / max {rt.max_mix_shift_pct:.2f}%)",
        ))

    if rt.regime_distribution:
        table_rows: List[tuple] = [("Regime", "Count", "Share %")]
        total = sum(rt.regime_distribution.values()) or 1
        for regime, count in sorted(
            rt.regime_distribution.items(), key=lambda kv: -kv[1]
        ):
            pct = (count / total) * 100
            table_rows.append((regime, str(count), f"{pct:.1f}%"))
        findings.append(ReviewFinding(
            "info",
            "Regime distribution (see table)",
        ))
    else:
        table_rows = None

    if rt.new_sessions:
        findings.append(ReviewFinding(
            "warn",
            f"New sessions appeared recently: {', '.join(sorted(rt.new_sessions))}",
        ))

    return ReviewSection(
        heading="4. Session / Regime Review",
        findings=findings,
        table=table_rows,
    )


def _suggestion_review(snap: StrategyHealthSnapshot, t: HealthTrendMetrics) -> ReviewSection:
    findings: List[ReviewFinding] = []
    table_rows: List[tuple] = [("Suggestion Type", "Occurrences", "Recurring")]

    triggered_suggestions = [s for s in snap.suggestions]
    if not triggered_suggestions and not t.suggestion_trends:
        return ReviewSection(heading="5. Suggestion Review", findings=[
            ReviewFinding("ok", "No active suggestions."),
        ])

    if triggered_suggestions:
        n = len(triggered_suggestions)
        findings.append(ReviewFinding(
            "info",
            f"{n} suggestion(s) currently active.",
        ))

    found_recurring = False
    for st in t.suggestion_trends:
        if st.suggestion_type == "any":
            continue  # special sentinel
        if st.is_recurring and st.entries_with_this_suggestion > 0:
            found_recurring = True
            findings.append(ReviewFinding(
                "warn",
                f"🟡 {st.suggestion_type}: appears in "
                f"{st.entries_with_this_suggestion}/{st.total_entries} entries "
                f"(recurring)",
            ))
            table_rows.append((
                st.suggestion_type,
                str(st.occurrences),
                "yes",
            ))
        elif st.suggestion_type != "any":
            table_rows.append((
                st.suggestion_type,
                str(st.occurrences),
                "no",
            ))

    if not found_recurring and not triggered_suggestions:
        findings.append(ReviewFinding("ok", "No recurring suggestions in window."))

    return ReviewSection(
        heading="5. Suggestion Review",
        findings=findings,
        table=table_rows if len(table_rows) > 1 else None,
    )


def _approval_review(snap: StrategyHealthSnapshot, t: HealthTrendMetrics) -> ReviewSection:
    findings: List[ReviewFinding] = []
    at = t.approval_trend

    if at is None or at.total_entries == 0:
        # Fall back to current snapshot
        pending = [a for a in snap.pending_approvals if a.status == "approval_pending"]
        if pending:
            findings.append(ReviewFinding(
                "warn",
                f"{len(pending)} approval(s) currently pending review.",
            ))
        else:
            findings.append(ReviewFinding("ok", "No pending approvals."))
        return ReviewSection(heading="6. Approval Review", findings=findings)

    findings.append(ReviewFinding(
        "info",
        f"Approval trend (window {at.window_days}d): "
        f"pending rate **{at.pending_rate_pct:.1f}%**, "
        f"approved rate **{at.approved_rate_pct:.1f}%**, "
        f"rejected rate **{at.rejected_rate_pct:.1f}%**",
    ))

    # Current backlog
    if at.current_pending > 0:
        findings.append(ReviewFinding(
            "warn",
            f"🟡 Current backlog: {at.current_pending} pending, "
            f"{at.current_approved} approved, {at.current_rejected} rejected",
        ))
    elif at.current_pending == 0:
        findings.append(ReviewFinding(
            "ok",
            "🟢 No current pending approvals.",
        ))

    table_rows: List[tuple] = [("Metric", "Count", "Rate %")]
    table_rows.append((
        "Pending",
        str(at.entries_with_pending),
        f"{at.pending_rate_pct:.1f}%",
    ))
    table_rows.append((
        "Approved",
        str(at.entries_with_approved),
        f"{at.approved_rate_pct:.1f}%",
    ))
    table_rows.append((
        "Rejected",
        str(at.entries_with_rejected),
        f"{at.rejected_rate_pct:.1f}%",
    ))

    return ReviewSection(
        heading="6. Approval Review",
        findings=findings,
        table=table_rows,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


def build_review_report(snap: StrategyHealthSnapshot, window_days: int = 7) -> ReviewReport:
    """Build a read-only review report aggregating current snapshot + history trends.

    Reuses `compute_all_trend_windows()` from the history module.
    Falls back to a current-snapshot-only summary when < 2 entries exist in window.

    Args:
        snap: The current StrategyHealthSnapshot.
        window_days: Which window to focus on (7 / 14 / 30). Defaults to 7.

    Returns:
        A ReviewReport with sections populated. Always succeeds.
    """
    windows = compute_all_trend_windows()
    trend = windows.get(f"{window_days}d")

    # Fall back if window missing or empty
    if trend is None:
        # Build a degenerate trend with no data
        trend = HealthTrendMetrics(window_days=window_days, computed_at_iso="", entries_used=0)
        has_history = False
    else:
        has_history = trend.entries_used >= 2

    if not has_history:
        # Graceful fallback: still surface what we know from the current snapshot
        # Build zero-filled trend and proceed
        trend = HealthTrendMetrics(
            window_days=window_days,
            computed_at_iso=trend.computed_at_iso or "",
            entries_used=trend.entries_used,
        )

    sections: List[ReviewSection] = [
        _exec_summary(snap, trend),
        _key_deteriorations(snap, trend),
        _repeated_diagnostics(snap, trend),
        _session_regime(snap, trend),
        _suggestion_review(snap, trend),
        _approval_review(snap, trend),
    ]

    if not has_history and snap.diagnostics:
        # Add a fallback note in the appropriate section (Session/Regime already shows this)
        sections[0].findings.insert(
            0,
            ReviewFinding(
                "info",
                "⚠️ Showing current-snapshot summary — history < 2 entries.",
            ),
        )

    return ReviewReport(
        window_days=window_days,
        sections=sections,
        has_sufficient_history=has_history,
        health_score=trend.health_score,
        health_status=snap.health_status,
        health_status_trend=(
            trend.health_status_trend.value
            if isinstance(trend.health_status_trend, TrendDirection)
            else str(trend.health_status_trend)
        ),
    )


def build_review_report_all_windows(snap: StrategyHealthSnapshot) -> dict:
    """Build review reports for all 3 windows (7d / 14d / 30d).

    Returns dict like {"7d": ReviewReport, "14d": ReviewReport, "30d": ReviewReport}.
    """
    return {f"{d}d": build_review_report(snap, d) for d in (7, 14, 30)}
