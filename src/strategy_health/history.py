"""History storage (append-only) + trend computation for Strategy Health.

Design principles:
- Append-only history file (one JSON line per entry = corruption-contained)
- Graceful fallback when history is empty or missing
- Trend computation is read-only (never modifies history)
- TrendSummary is derived from history, not stored alongside
- No auto-append on load — caller decides when to record a snapshot

Usage::

    from src.strategy_health.history import (
        append_snapshot_to_history,
        load_history_entries,
        compute_trend_window,
        compute_all_trend_windows,
        generate_trend_report_markdown,
        generate_trend_report_csv,
    )

    # Record current snapshot
    snap = load_latest_snapshot()
    append_snapshot_to_history(snap)

    # Compute trends
    trends_7d  = compute_trend_window(days=7)
    trends_30d = compute_all_trend_windows()
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from .history_models import (
    ApprovalTrend,
    DiagnosticTrend,
    HealthHistoryEntry,
    HealthTrendMetrics,
    RegimeTrend,
    SuggestionTrend,
    TrendDirection,
)
from .models import StrategyHealthSnapshot
from .snapshot_loader import load_latest_snapshot

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

HISTORY_DIR = Path(__file__).parents[3] / "data" / "strategy_health"
HISTORY_FILE = HISTORY_DIR / "history.jsonl"


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: History Entry Creation
# ─────────────────────────────────────────────────────────────────────────────

def snapshot_to_history_entry(
    snap: StrategyHealthSnapshot,
) -> HealthHistoryEntry:
    """Derive a compact HealthHistoryEntry from a StrategyHealthSnapshot.

    This is the only bridge between the live snapshot and the history store.
    Call this before appending to history.
    """
    from .models import SEVERITY_CRITICAL, SEVERITY_WARN, SEVERITY_OK

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Tally severities
    warn_count = sum(1 for d in snap.diagnostics if d.severity == SEVERITY_WARN)
    critical_count = sum(1 for d in snap.diagnostics if d.severity == SEVERITY_CRITICAL)
    unknown_count = sum(1 for d in snap.diagnostics if d.severity == SEVERITY_OK)  # cfg default

    # Extract key metrics from diagnostics
    regime_mix_shift = -1.0
    avg_hit_rate = -1.0
    max_dd = -1.0
    cost_drag = -1.0
    oldest_age = -1.0
    dominant_regime = "unknown"

    regime_metrics: dict = {}
    perf_metrics: dict = {}

    for diag in snap.diagnostics:
        m = diag.metrics or {}
        name = diag.name
        if name == "regime":
            regime_mix_shift = float(m.get("regime_mix_shift_pct", -1.0))
            regime_metrics = m
        elif name == "performance":
            avg_hit_rate = float(m.get("hit_rate_pct", -1.0))
            perf_metrics = m
        elif name == "drawdown":
            max_dd = float(m.get("max_dd_pct", -1.0))
        elif name == "cost":
            cost_drag = float(m.get("cost_drag_pct", -1.0))
        elif name == "freshness":
            oldest_age = float(m.get("oldest_data_age_minutes", -1.0))

    # Dominant regime from breakdown
    if regime_metrics and "regime_breakdown" in regime_metrics:
        breakdown = regime_metrics["regime_breakdown"]
        if breakdown:
            dominant_regime = max(breakdown, key=lambda k: breakdown[k]["n"])

    # Approval counts
    pending = snap.approval_state.pending_count
    approved = snap.approval_state.approved_count
    rejected = snap.approval_state.rejected_count

    return HealthHistoryEntry(
        timestamp_iso=now_iso,
        health_status=snap.health_status,
        diagnostic_count=len(snap.diagnostics),
        warn_count=warn_count,
        critical_count=critical_count,
        unknown_count=unknown_count,
        suggestion_count=len(snap.suggestions),
        dominant_regime=dominant_regime,
        regime_mix_shift_pct=regime_mix_shift,
        avg_hit_rate_pct=avg_hit_rate,
        max_drawdown_pct=max_dd,
        cost_drag_pct=cost_drag,
        oldest_data_age_minutes=oldest_age,
        pending_approvals=pending,
        approved_approvals=approved,
        rejected_approvals=rejected,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: History Storage (append-only, atomic)
# ─────────────────────────────────────────────────────────────────────────────

def append_snapshot_to_history(snap: StrategyHealthSnapshot) -> bool:
    """Append a snapshot to the history file (append-only).

    Uses atomic write (temp file + rename) so a crash mid-write cannot corrupt
    the existing history.

    Args:
        snap: StrategyHealthSnapshot from build_health_snapshot()

    Returns:
        True if appended successfully, False if history file is not accessible
        (e.g., history dir missing). Never raises.

    Raises:
        Nothing — all errors are swallowed and False is returned.
    """
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    entry = snapshot_to_history_entry(snap)
    line = json.dumps(entry.to_dict(), sort_keys=True) + "\n"

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(HISTORY_DIR), delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(line)
            tmp_path = tmp.name

        os.replace(tmp_path, HISTORY_FILE)  # atomic on POSIX; best-effort on Windows
        return True
    except OSError:
        return False


def append_entry_to_history(entry: HealthHistoryEntry) -> bool:
    """Append a pre-built HealthHistoryEntry directly (no snapshot needed)."""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    line = json.dumps(entry.to_dict(), sort_keys=True) + "\n"
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except OSError:
        return False


def load_history_entries(
    limit: Optional[int] = None,
    since_iso: Optional[str] = None,
) -> List[HealthHistoryEntry]:
    """Load all history entries, newest first.

    Errors (corrupt lines) are skipped — the file remains readable.

    Args:
        limit: Maximum number of entries to return (newest first).
        since_iso: Optional ISO8601 timestamp string; only entries at/after this
            time are returned.

    Returns:
        List of HealthHistoryEntry, newest first. Empty list if file missing.
    """
    if not HISTORY_FILE.exists():
        return []

    entries: List[HealthHistoryEntry] = []
    since_dt: Optional[datetime] = None
    if since_iso:
        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entry = HealthHistoryEntry.from_dict(d)
                    if since_dt:
                        entry_dt = datetime.fromisoformat(
                            entry.timestamp_iso.replace("Z", "+00:00")
                        )
                        if entry_dt < since_dt:
                            continue
                    entries.append(entry)
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Skip corrupt lines — contained corruption
                    continue
    except OSError:
        return []

    entries.sort(key=lambda e: e.timestamp_iso, reverse=True)

    if limit:
        entries = entries[:limit]

    return entries


def iter_history_entries() -> Iterator[HealthHistoryEntry]:
    """Yield history entries one at a time, newest first. Lazy (streaming).

    Stops at first corrupt line. For full recovery, use load_history_entries().
    """
    if not HISTORY_FILE.exists():
        return

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield HealthHistoryEntry.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError, ValueError):
                    break
    except OSError:
        return


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Trend Computation
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    """Parse ISO8601 timestamp, return offset-aware UTC datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _trend_direction(first: float, last: float, threshold_pct: float = 10.0) -> TrendDirection:
    """Determine trend direction from first/last values (both positive = higher is better).

    For metrics where lower is better (drawdown, cost drag), swap the comparison.
    """
    if first < 0 or last < 0:
        return TrendDirection.UNKNOWN

    if first == last:
        return TrendDirection.STABLE

    change_pct = abs(last - first) / max(abs(first), 0.001) * 100.0
    if change_pct < threshold_pct:
        return TrendDirection.STABLE

    # Higher is better: last > first = improving
    # (overridden per metric type in caller)
    return TrendDirection.IMPROVING if last > first else TrendDirection.DEGRADING


def compute_diagnostic_trends(
    entries: List[HealthHistoryEntry],
    window_days: int,
) -> tuple[DiagnosticTrend, ...]:
    """Compute per-diagnostic severity persistence trends.

    For v1, diagnostic-level trends are derived from the aggregated
    warn_count / critical_count per entry (not individual diagnostic severities).
    Individual diagnostic trends are stored per-entry for future expansion.
    """
    if not entries:
        return ()

    # Group by health trend derived signals
    # v1: derive a simplified diagnostic-level view from warn/critical counts
    warn_days = sum(1 for e in entries if e.warn_count > 0 and e.critical_count == 0)
    crit_days = sum(1 for e in entries if e.critical_count > 0)
    ok_days = sum(1 for e in entries if e.warn_count == 0 and e.critical_count == 0)
    total = len(entries)

    trend = DiagnosticTrend(
        diagnostic_name="overall",
        window_days=window_days,
        warn_days=warn_days,
        critical_days=crit_days,
        ok_days=ok_days,
        total_entries=total,
        warn_persistence_pct=round(warn_days / total * 100, 1) if total else -1.0,
        critical_persistence_pct=round(crit_days / total * 100, 1) if total else -1.0,
        dominant_severity=_dominant_severity(crit_days, warn_days, ok_days),
    )

    # Per-metric trends
    metric_trends = _compute_metric_trends(entries)

    return (
        DiagnosticTrend(
            diagnostic_name="performance",
            window_days=window_days,
            warn_days=0, critical_days=0, ok_days=0, total_entries=total,
            warn_persistence_pct=-1.0, critical_persistence_pct=-1.0,
            metric_trend=metric_trends.get("avg_hit_rate", TrendDirection.UNKNOWN),
            metric_trend_strength=-1.0,
            dominant_severity="ok",
        ),
        DiagnosticTrend(
            diagnostic_name="regime",
            window_days=window_days,
            warn_days=0, critical_days=0, ok_days=0, total_entries=total,
            warn_persistence_pct=-1.0, critical_persistence_pct=-1.0,
            metric_trend=metric_trends.get("regime_mix_shift", TrendDirection.UNKNOWN),
            metric_trend_strength=-1.0,
            dominant_severity="ok",
        ),
        DiagnosticTrend(
            diagnostic_name="drawdown",
            window_days=window_days,
            warn_days=0, critical_days=0, ok_days=0, total_entries=total,
            warn_persistence_pct=-1.0, critical_persistence_pct=-1.0,
            metric_trend=metric_trends.get("max_drawdown", TrendDirection.UNKNOWN),
            metric_trend_strength=-1.0,
            dominant_severity="ok",
        ),
        DiagnosticTrend(
            diagnostic_name="cost",
            window_days=window_days,
            warn_days=0, critical_days=0, ok_days=0, total_entries=total,
            warn_persistence_pct=-1.0, critical_persistence_pct=-1.0,
            metric_trend=metric_trends.get("cost_drag", TrendDirection.UNKNOWN),
            metric_trend_strength=-1.0,
            dominant_severity="ok",
        ),
        trend,  # overall diagnostic
    )


def _dominant_severity(crit: int, warn: int, ok: int) -> str:
    if crit > 0:
        return "critical"
    if warn > 0:
        return "warn"
    if ok > 0:
        return "ok"
    return "unknown"


def _compute_metric_trends(entries: List[HealthHistoryEntry]) -> dict:
    """Compute per-metric trend directions."""
    results = {}

    hit_rates = [e.avg_hit_rate_pct for e in entries if e.avg_hit_rate_pct >= 0]
    if len(hit_rates) >= 2:
        first, last = hit_rates[-1], hit_rates[0]  # oldest, newest (sorted newest-first)
        direction = TrendDirection.IMPROVING if last > first else TrendDirection.DEGRADING if last < first else TrendDirection.STABLE
        results["avg_hit_rate"] = direction

    mix_shifts = [e.regime_mix_shift_pct for e in entries if e.regime_mix_shift_pct >= 0]
    if len(mix_shifts) >= 2:
        first, last = mix_shifts[-1], mix_shifts[0]
        direction = TrendDirection.IMPROVING if last < first else TrendDirection.DEGRADING if last > first else TrendDirection.STABLE
        results["regime_mix_shift"] = direction

    max_dds = [e.max_drawdown_pct for e in entries if e.max_drawdown_pct >= 0]
    if len(max_dds) >= 2:
        first, last = max_dds[-1], max_dds[0]
        # lower drawdown = better
        direction = TrendDirection.IMPROVING if last < first else TrendDirection.DEGRADING if last > first else TrendDirection.STABLE
        results["max_drawdown"] = direction

    cost_drags = [e.cost_drag_pct for e in entries if e.cost_drag_pct >= 0]
    if len(cost_drags) >= 2:
        first, last = cost_drags[-1], cost_drags[0]
        # lower cost = better
        direction = TrendDirection.IMPROVING if last < first else TrendDirection.DEGRADING if last > first else TrendDirection.STABLE
        results["cost_drag"] = direction

    return results


def compute_suggestion_trends(
    entries: List[HealthHistoryEntry],
    window_days: int,
) -> tuple[SuggestionTrend, ...]:
    """Compute suggestion frequency and recurrence trends.

    Note: history entries only store suggestion_count (not types).
    For full type-level suggestion trends, snapshot would need to store
    suggestion types — v1 aggregates by count only.
    """
    if not entries:
        return ()

    total_entries = len(entries)
    entries_with_suggestions = [e for e in entries if e.suggestion_count > 0]

    if not entries_with_suggestions:
        return (
            SuggestionTrend(
                suggestion_type="any",
                window_days=window_days,
                occurrences=0,
                appearances_per_day=0.0,
                trend=TrendDirection.STABLE,
                is_recurring=False,
                entries_with_this_suggestion=0,
            ),
        )

    total_occurrences = sum(e.suggestion_count for e in entries_with_suggestions)
    days_span = _days_span(entries)
    rate = total_occurrences / max(days_span, 1)

    # Trend: is suggestion frequency increasing or decreasing?
    recent_half = entries[:len(entries) // 2]
    older_half = entries[len(entries) // 2:]
    recent_avg = sum(e.suggestion_count for e in recent_half) / max(len(recent_half), 1)
    older_avg = sum(e.suggestion_count for e in older_half) / max(len(older_half), 1)

    if recent_avg > older_avg * 1.2:
        trend = TrendDirection.DEGRADING  # more suggestions = worse
    elif recent_avg < older_avg * 0.8:
        trend = TrendDirection.IMPROVING
    else:
        trend = TrendDirection.STABLE

    return (
        SuggestionTrend(
            suggestion_type="any",
            window_days=window_days,
            occurrences=total_occurrences,
            appearances_per_day=round(rate, 2),
            trend=trend,
            is_recurring=len(entries_with_suggestions) >= 2,
            entries_with_this_suggestion=len(entries_with_suggestions),
        ),
    )


def compute_approval_trends(
    entries: List[HealthHistoryEntry],
    window_days: int,
) -> Optional[ApprovalTrend]:
    """Compute approval distribution trend."""
    if not entries:
        return None

    total = len(entries)
    with_pending = sum(1 for e in entries if e.pending_approvals > 0)
    with_approved = sum(1 for e in entries if e.approved_approvals > 0)
    with_rejected = sum(1 for e in entries if e.rejected_approvals > 0)

    # Current counts (from newest entry)
    current = entries[0]
    current_pending = current.pending_approvals
    current_approved = current.approved_approvals
    current_rejected = current.rejected_approvals

    return ApprovalTrend(
        window_days=window_days,
        total_entries=total,
        entries_with_pending=with_pending,
        entries_with_approved=with_approved,
        entries_with_rejected=with_rejected,
        pending_rate_pct=round(with_pending / total * 100, 1) if total else -1.0,
        approved_rate_pct=round(with_approved / total * 100, 1) if total else -1.0,
        rejected_rate_pct=round(with_rejected / total * 100, 1) if total else -1.0,
        current_pending=current_pending,
        current_approved=current_approved,
        current_rejected=current_rejected,
    )


def compute_regime_trends(
    entries: List[HealthHistoryEntry],
    window_days: int,
) -> Optional[RegimeTrend]:
    """Compute regime distribution + drift trend."""
    if not entries:
        return None

    total = len(entries)

    mix_shifts = [e.regime_mix_shift_pct for e in entries if e.regime_mix_shift_pct >= 0]
    avg_shift = round(sum(mix_shifts) / len(mix_shifts), 1) if mix_shifts else -1.0
    max_shift = max(mix_shifts) if mix_shifts else -1.0

    regimes = [e.dominant_regime for e in entries if e.dominant_regime != "unknown"]
    regime_dist = dict(Counter(regimes))
    dominant = max(regime_dist, key=regime_dist.get) if regime_dist else "unknown"

    # Regime stability: how often did the dominant regime stay the same?
    if regimes:
        stability = round(regimes.count(dominant) / len(regimes) * 100, 1)
    else:
        stability = -1.0

    # Mix shift trend
    if len(mix_shifts) >= 2:
        first, last = mix_shifts[-1], mix_shifts[0]
        # lower mix shift = more stable = better
        shift_trend = (
            TrendDirection.IMPROVING if last < first
            else TrendDirection.DEGRADING if last > first
            else TrendDirection.STABLE
        )
    else:
        shift_trend = TrendDirection.UNKNOWN

    return RegimeTrend(
        window_days=window_days,
        total_entries=total,
        avg_mix_shift_pct=avg_shift,
        max_mix_shift_pct=max_shift,
        mix_shift_trend=shift_trend,
        dominant_regime=dominant,
        regime_distribution=regime_dist,
        regime_stability_pct=stability,
        session_set=(),
        new_sessions=(),
    )


def _days_span(entries: List[HealthHistoryEntry]) -> float:
    """Days between newest and oldest entry."""
    if len(entries) < 2:
        return 1.0
    newest = _parse_ts(entries[0].timestamp_iso)
    oldest = _parse_ts(entries[-1].timestamp_iso)
    delta = newest - oldest
    return max(delta.total_seconds() / 86400.0, 1.0)


def compute_health_score(trends: HealthTrendMetrics) -> float:
    """Composite health score 0-100.

    Heuristic:
    - 40% from health status (green/yellow/red distribution)
    - 30% from suggestion rate (fewer = better)
    - 20% from performance trend
    - 10% from drawdown trend
    """
    if trends.entries_used == 0:
        return -1.0

    # Health status score
    total = trends.green_days + trends.yellow_days + trends.red_days + trends.unknown_days
    if total == 0:
        status_score = 50.0
    else:
        status_score = (
            trends.green_days * 1.0
            + trends.yellow_days * 0.6
            + trends.red_days * 0.0
            + trends.unknown_days * 0.3
        ) / total * 100

    # Suggestion score (based on suggestion_trends if available)
    sugg_trends = trends.suggestion_trends
    if sugg_trends:
        any_trend = sugg_trends[0]
        sugg_rate = any_trend.appearances_per_day  # suggestions per day
        sugg_score = max(0, 100 - sugg_rate * 20)  # rough: 5 suggestions/day = 0 score
    else:
        sugg_score = 100.0

    # Performance trend score
    diag_trends = {d.diagnostic_name: d for d in trends.diagnostic_trends}
    perf_trend = diag_trends.get("performance")
    if perf_trend and perf_trend.metric_trend != TrendDirection.UNKNOWN:
        perf_score = (
            100.0 if perf_trend.metric_trend == TrendDirection.IMPROVING
            else 50.0 if perf_trend.metric_trend == TrendDirection.STABLE
            else 20.0
        )
    else:
        perf_score = 75.0

    # Drawdown trend score
    dd_trend = diag_trends.get("drawdown")
    if dd_trend and dd_trend.metric_trend != TrendDirection.UNKNOWN:
        dd_score = (
            100.0 if dd_trend.metric_trend == TrendDirection.IMPROVING  # lower dd = better
            else 50.0 if dd_trend.metric_trend == TrendDirection.STABLE
            else 20.0
        )
    else:
        dd_score = 75.0

    return round(
        status_score * 0.40
        + sugg_score * 0.30
        + perf_score * 0.20
        + dd_score * 0.10,
        1,
    )


def compute_trend_window(
    days: int = 7,
    entries: Optional[List[HealthHistoryEntry]] = None,
) -> HealthTrendMetrics:
    """Compute full trend summary for the given window.

    Args:
        days: Time window in days.
        entries: Pre-loaded entries (newest first). If None, loads from history.

    Returns:
        HealthTrendMetrics for the window. Never raises.
    """
    if entries is None:
        since = datetime.now(tz=timezone.utc) - timedelta(days=days)
        entries = load_history_entries(since_iso=since.strftime("%Y-%m-%dT%H:%M:%SZ"))

    total = len(entries)

    # Filter to window by time
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    window_entries = [
        e for e in entries
        if _parse_ts(e.timestamp_iso) >= cutoff
    ]
    # Also filter by pre-loaded since (already done above)

    if window_entries:
        total = len(window_entries)
        entries = window_entries

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Health status distribution
    green_days = sum(1 for e in entries if e.health_status == "green")
    yellow_days = sum(1 for e in entries if e.health_status == "yellow")
    red_days = sum(1 for e in entries if e.health_status == "red")
    unknown_days = sum(1 for e in entries if e.health_status == "unknown")

    # Health trend direction
    if total >= 2:
        first_status = entries[-1].health_status
        last_status = entries[0].health_status
        status_order = {"green": 3, "yellow": 2, "red": 1, "unknown": 0}
        first_val = status_order.get(first_status, 0)
        last_val = status_order.get(last_status, 0)
        if last_val > first_val:
            health_trend = TrendDirection.IMPROVING
        elif last_val < first_val:
            health_trend = TrendDirection.DEGRADING
        else:
            health_trend = TrendDirection.STABLE
    else:
        health_trend = TrendDirection.INSUFFICIENT_DATA

    diag_trends = compute_diagnostic_trends(entries, days)
    sugg_trends = compute_suggestion_trends(entries, days)
    appr_trend = compute_approval_trends(entries, days)
    reg_trend = compute_regime_trends(entries, days)

    prelim = HealthTrendMetrics(
        window_days=days,
        computed_at_iso=now_iso,
        entries_used=total,
        green_days=green_days,
        yellow_days=yellow_days,
        red_days=red_days,
        unknown_days=unknown_days,
        health_status_trend=health_trend,
        diagnostic_trends=diag_trends,
        suggestion_trends=sugg_trends,
        approval_trend=appr_trend,
        regime_trend=reg_trend,
    )

    health_score = compute_health_score(prelim)
    summary = _summarize_trends(prelim)

    return HealthTrendMetrics(
        window_days=days,
        computed_at_iso=now_iso,
        entries_used=total,
        green_days=green_days,
        yellow_days=yellow_days,
        red_days=red_days,
        unknown_days=unknown_days,
        health_status_trend=health_trend,
        diagnostic_trends=diag_trends,
        suggestion_trends=sugg_trends,
        approval_trend=appr_trend,
        regime_trend=reg_trend,
        health_score=health_score,
        summary=summary,
    )


def _summarize_trends(t: HealthTrendMetrics) -> str:
    """Generate a human-readable one-line summary."""
    parts = []
    status_map = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⚪"}
    parts.append(f"{t.entries_used} entries")

    if t.health_score >= 0:
        parts.append(f"score {t.health_score}")

    trend_map = {
        TrendDirection.IMPROVING: "↑",
        TrendDirection.STABLE: "→",
        TrendDirection.DEGRADING: "↓",
        TrendDirection.UNKNOWN: "?",
        TrendDirection.INSUFFICIENT_DATA: "·",
    }
    parts.append(f"trend {trend_map.get(t.health_status_trend, '?')}")

    if t.regime_trend and t.regime_trend.dominant_regime != "unknown":
        parts.append(f"regime {t.regime_trend.dominant_regime}")

    if t.suggestion_trends and t.suggestion_trends[0].occurrences > 0:
        parts.append(f"{t.suggestion_trends[0].occurrences} suggestions")

    return " | ".join(parts)


def compute_all_trend_windows() -> dict:
    """Compute trend summaries for 7, 14, and 30-day windows.

    Returns:
        dict with keys "7d", "14d", "30d" → HealthTrendMetrics.
    """
    return {
        "7d": compute_trend_window(days=7),
        "14d": compute_trend_window(days=14),
        "30d": compute_trend_window(days=30),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Report Generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_trend_report_markdown(trends: dict) -> str:
    """Generate a markdown trend report from compute_all_trend_windows() output.

    Args:
        trends: {"7d": HealthTrendMetrics, "14d": ..., "30d": ...}
    """
    lines = [
        "# Strategy Health Trend Report",
        f"_Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Window | Entries | Score | Trend | Green | Yellow | Red |")
    lines.append("|--------|---------|-------|-------|-------|--------|-----|")
    for label, t in trends.items():
        score = f"{t.health_score:.0f}" if t.health_score >= 0 else "N/A"
        trend_arrow = {
            TrendDirection.IMPROVING: "↑",
            TrendDirection.STABLE: "→",
            TrendDirection.DEGRADING: "↓",
            TrendDirection.UNKNOWN: "?",
            TrendDirection.INSUFFICIENT_DATA: "·",
        }.get(t.health_status_trend, "?")
        lines.append(
            f"| {label} | {t.entries_used} | {score} | {trend_arrow} "
            f"| {t.green_days} | {t.yellow_days} | {t.red_days} |"
        )
    lines.append("")

    # Per-window detail
    for label, t in trends.items():
        lines.append(f"## {label.upper()} Detail")
        lines.append("")
        lines.append(f"**Summary:** {t.summary}")
        lines.append("")

        # Diagnostic trends
        if t.diagnostic_trends:
            lines.append("### Diagnostic Trends")
            lines.append("")
            lines.append("| Diagnostic | Dominant Severity | Warn Days | Crit Days | Metric Trend |")
            lines.append("|------------|-------------------|-----------|-----------|--------------|")
            for d in t.diagnostic_trends:
                if d.diagnostic_name == "overall":
                    continue
                trend_str = d.metric_trend.value if d.metric_trend else "unknown"
                lines.append(
                    f"| {d.diagnostic_name} | {d.dominant_severity} | "
                    f"{d.warn_days} | {d.critical_days} | {trend_str} |"
                )
            lines.append("")

        # Overall diagnostic
        overall = next((d for d in t.diagnostic_trends if d.diagnostic_name == "overall"), None)
        if overall:
            lines.append(f"**Overall:** {overall.warn_days} warn days, {overall.critical_days} critical days, "
                         f"warn persistence {overall.warn_persistence_pct}%")
            lines.append("")

        # Regime trend
        if t.regime_trend:
            lines.append("### Regime")
            lines.append("")
            r = t.regime_trend
            lines.append(f"- Dominant: **{r.dominant_regime}** (stability: {r.regime_stability_pct}%)")
            lines.append(f"- Avg mix shift: {r.avg_mix_shift_pct}% | Max: {r.max_mix_shift_pct}%")
            lines.append(f"- Mix shift trend: {r.mix_shift_trend.value}")
            if r.regime_distribution:
                dist_str = ", ".join(f"{k}: {v}" for k, v in r.regime_distribution.items())
                lines.append(f"- Distribution: {dist_str}")
            lines.append("")

        # Approval trend
        if t.approval_trend:
            lines.append("### Approvals")
            lines.append("")
            a = t.approval_trend
            lines.append(f"- Pending: {a.current_pending} | Approved: {a.current_approved} | Rejected: {a.current_rejected}")
            lines.append(f"- Pending rate: {a.pending_rate_pct}% | Approved rate: {a.approved_rate_pct}%")
            lines.append("")

        # Suggestion trend
        if t.suggestion_trends:
            lines.append("### Suggestions")
            lines.append("")
            s = t.suggestion_trends[0]
            lines.append(f"- Total occurrences: {s.occurrences} | Per day: {s.appearances_per_day}")
            lines.append(f"- Trend: {s.trend.value} | Recurring: {'Yes' if s.is_recurring else 'No'}")
            lines.append("")

    return "\n".join(lines)


def generate_trend_report_csv(trends: dict) -> str:
    """Generate a CSV report from compute_all_trend_windows() output."""
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "window", "entries_used", "health_score", "health_trend",
        "green_days", "yellow_days", "red_days", "unknown_days",
        "diagnostic_warn_days", "diagnostic_critical_days",
        "dominant_regime", "avg_mix_shift", "mix_shift_trend",
        "suggestion_occurrences", "suggestion_per_day", "suggestion_trend",
        "approval_pending", "approval_approved", "approval_rejected",
        "computed_at_iso",
    ])

    for label, t in trends.items():
        overall = next((d for d in t.diagnostic_trends if d.diagnostic_name == "overall"), None)
        reg = t.regime_trend
        sugg = t.suggestion_trends[0] if t.suggestion_trends else None
        appr = t.approval_trend

        writer.writerow([
            label,
            t.entries_used,
            t.health_score if t.health_score >= 0 else "",
            t.health_status_trend.value,
            t.green_days,
            t.yellow_days,
            t.red_days,
            t.unknown_days,
            overall.warn_days if overall else "",
            overall.critical_days if overall else "",
            reg.dominant_regime if reg else "",
            reg.avg_mix_shift_pct if reg else "",
            reg.mix_shift_trend.value if reg else "",
            sugg.occurrences if sugg else "",
            sugg.appearances_per_day if sugg else "",
            sugg.trend.value if sugg else "",
            appr.current_pending if appr else "",
            appr.current_approved if appr else "",
            appr.rejected_approvals if appr else "",
            t.computed_at_iso,
        ])

    return buf.getvalue()