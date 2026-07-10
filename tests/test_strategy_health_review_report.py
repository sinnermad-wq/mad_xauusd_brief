"""Tests for strategy_health.review_report — read-only dashboard summary."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.strategy_health.models import (
    HEALTH_GREEN,
    HEALTH_YELLOW,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARN,
    StrategyApprovalState,
    StrategyDiagnostic,
    StrategyHealthSnapshot,
    StrategySuggestion,
)
from src.strategy_health.review_report import (
    ReviewFinding,
    ReviewReport,
    ReviewSection,
    build_review_report,
    build_review_report_all_windows,
)


def _make_snap(
    health_status: str = HEALTH_GREEN,
    diagnostics: tuple | None = None,
    suggestions: tuple | None = None,
    pending_approvals: tuple = (),
    generated_at: str | None = None,
) -> StrategyHealthSnapshot:
    if diagnostics is None:
        diagnostics = (
            StrategyDiagnostic(name="performance", severity=SEVERITY_OK, summary="ok"),
            StrategyDiagnostic(name="drawdown", severity=SEVERITY_OK, summary="ok"),
        )
    if suggestions is None:
        suggestions = ()
    if generated_at is None:
        generated_at = datetime.now(tz=timezone.utc).isoformat()
    return StrategyHealthSnapshot(
        snapshot_id="test-review-snap",
        generated_at=generated_at,
        health_status=health_status,
        diagnostics=diagnostics,
        suggestions=suggestions,
        pending_approvals=pending_approvals,
    )


# ─────────────────────────────────────────────────────────────────
# ReviewFinding
# ─────────────────────────────────────────────────────────────────


class TestReviewFinding:
    def test_display_ok_icon(self):
        f = ReviewFinding(severity="ok", text="All clear")
        assert "🟢" in f.display()
        assert "All clear" in f.display()

    def test_display_warn_icon(self):
        f = ReviewFinding(severity="warn", text="Caution")
        assert "🟡" in f.display()

    def test_display_critical_icon(self):
        f = ReviewFinding(severity="critical", text="Danger")
        assert "🔴" in f.display()

    def test_display_unknown_severity(self):
        f = ReviewFinding(severity="weird", text="Unknown")
        out = f.display()
        assert "•" in out


# ─────────────────────────────────────────────────────────────────
# ReviewSection
# ─────────────────────────────────────────────────────────────────


class TestReviewSection:
    def test_empty_no_content(self):
        sec = ReviewSection(heading="Empty")
        assert not sec.has_content()

    def test_with_findings(self):
        sec = ReviewSection(
            heading="X",
            findings=[ReviewFinding("ok", "y")],
        )
        assert sec.has_content()

    def test_with_table(self):
        sec = ReviewSection(
            heading="X",
            findings=[],
            table=[("a", "b"), ("1", "2")],
        )
        assert sec.has_content()


# ─────────────────────────────────────────────────────────────────
# ReviewReport.to_markdown
# ─────────────────────────────────────────────────────────────────


class TestReviewReportToMarkdown:
    def test_basic_markdown(self):
        r = ReviewReport(
            window_days=7,
            sections=[
                ReviewSection(
                    heading="Test",
                    findings=[ReviewFinding("ok", "All clear")],
                ),
            ],
            has_sufficient_history=True,
            health_score=85.0,
            health_status=HEALTH_GREEN,
            health_status_trend="stable",
        )
        md = r.to_markdown()
        assert "Strategy Health Review" in md
        assert "7d" in md
        assert "All clear" in md
        assert "85.0" in md or "85" in md

    def test_insufficient_history_banner(self):
        r = ReviewReport(
            window_days=7,
            sections=[],
            has_sufficient_history=False,
        )
        md = r.to_markdown()
        assert "Insufficient history" in md or "snapshot summary" in md.lower()

    def test_table_renders_as_markdown(self):
        r = ReviewReport(
            window_days=7,
            sections=[
                ReviewSection(
                    heading="T",
                    table=[("h1", "h2"), ("v1", "v2")],
                ),
            ],
        )
        md = r.to_markdown()
        assert "| h1 | h2 |" in md
        assert "| v1 | v2 |" in md


# ─────────────────────────────────────────────────────────────────
# build_review_report (integration)
# ─────────────────────────────────────────────────────────────────


class TestBuildReviewReport:
    def test_with_empty_history_graceful_fallback(self, tmp_path):
        """Less than 2 history entries → graceful fallback to snapshot summary."""
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _make_snap(health_status=HEALTH_GREEN)
            r = build_review_report(snap, window_days=7)
            assert isinstance(r, ReviewReport)
            assert r.window_days == 7
            # 6 sections always present
            assert len(r.sections) == 6
            section_headings = [s.heading for s in r.sections]
            assert "1. Executive Summary" in section_headings
            assert "6. Approval Review" in section_headings
        finally:
            h.HISTORY_FILE = orig

    def test_with_history_full_report(self, tmp_path):
        """With seeded history, all sections populate meaningfully."""
        import src.strategy_health.history as h
        from src.strategy_health.history_models import HealthTrendMetrics, TrendDirection
        from datetime import timedelta

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            now = datetime.now(tz=timezone.utc)
            # Seed 5 history entries spanning recent days
            for i in range(5):
                dt = now - timedelta(days=i)
                snap = _make_snap(
                    health_status=HEALTH_GREEN,
                    generated_at=dt.isoformat(),
                )
                h.append_entry_to_history(h.snapshot_to_history_entry(snap))

            snap = _make_snap(health_status=HEALTH_GREEN)
            r = build_review_report(snap, window_days=7)

            assert r.window_days == 7
            assert len(r.sections) == 6
            # Exec summary should mention health status
            exec_sec = next(s for s in r.sections if s.heading.startswith("1."))
            assert any("GREEN" in f.text.upper() for f in exec_sec.findings)
        finally:
            h.HISTORY_FILE = orig

    def test_deteriorations_section_includes_critical(self):
        snap = _make_snap(
            health_status=HEALTH_YELLOW,
            diagnostics=(
                StrategyDiagnostic(name="performance", severity=SEVERITY_CRITICAL, summary="bad"),
                StrategyDiagnostic(name="regime", severity=SEVERITY_OK, summary="ok"),
            ),
        )
        r = build_review_report(snap, window_days=7)
        det = next(s for s in r.sections if s.heading.startswith("2."))
        # Should have critical findings or table rows
        has_critical_text = any(f.severity == "critical" for f in det.findings)
        has_table = det.table is not None
        assert has_critical_text or has_table

    def test_repeated_diagnostics_with_warnings(self, tmp_path):
        """With seeded history containing warn days, the repeated diagnostics section surfaces them."""
        import src.strategy_health.history as h
        from datetime import timedelta

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            now = datetime.now(tz=timezone.utc)
            for i in range(5):
                dt = now - timedelta(days=i)
                snap = _make_snap(
                    health_status=HEALTH_YELLOW,
                    diagnostics=(
                        StrategyDiagnostic(
                            name="performance",
                            severity=SEVERITY_WARN,
                            summary="warn",
                        ),
                    ),
                    generated_at=dt.isoformat(),
                )
                h.append_entry_to_history(h.snapshot_to_history_entry(snap))

            snap = _make_snap(health_status=HEALTH_YELLOW)
            r = build_review_report(snap, window_days=14)
            rep = next(s for s in r.sections if s.heading.startswith("3."))
            # Findings should include warn severity OR the table should populate
            has_warn = any(f.severity == "warn" for f in rep.findings)
            has_table_data = rep.table is not None and len(rep.table) > 1
            assert has_warn or has_table_data
        finally:
            h.HISTORY_FILE = orig

    def test_suggestion_review_section_present(self):
        snap = _make_snap()
        r = build_review_report(snap, window_days=7)
        sugg = next(s for s in r.sections if s.heading.startswith("5."))
        assert isinstance(sugg, ReviewSection)
        # Always has at least the "no active suggestions" or some findings
        assert sugg.has_content() or any(
            f.severity == "ok" for f in sugg.findings
        )

    def test_approval_review_section_present(self):
        snap = _make_snap()
        r = build_review_report(snap, window_days=7)
        appr = next(s for s in r.sections if s.heading.startswith("6."))
        assert isinstance(appr, ReviewSection)

    def test_session_regime_review_present(self):
        snap = _make_snap()
        r = build_review_report(snap, window_days=7)
        sess = next(s for s in r.sections if s.heading.startswith("4."))
        assert isinstance(sess, ReviewSection)

    def test_window_days_respected(self, tmp_path):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _make_snap(health_status=HEALTH_GREEN)
            for d in [7, 14, 30]:
                r = build_review_report(snap, window_days=d)
                assert r.window_days == d
        finally:
            h.HISTORY_FILE = orig

    def test_report_to_markdown_renders_sections(self):
        snap = _make_snap()
        r = build_review_report(snap, window_days=7)
        md = r.to_markdown()
        # Should mention at least one section heading
        assert "Executive Summary" in md


# ─────────────────────────────────────────────────────────────────
# build_review_report_all_windows
# ─────────────────────────────────────────────────────────────────


class TestBuildReviewReportAllWindows:
    def test_returns_three_reports(self, tmp_path):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _make_snap()
            reports = build_review_report_all_windows(snap)
            assert set(reports.keys()) == {"7d", "14d", "30d"}
            for r in reports.values():
                assert isinstance(r, ReviewReport)
        finally:
            h.HISTORY_FILE = orig

    def test_each_window_includes_six_sections(self, tmp_path):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _make_snap()
            reports = build_review_report_all_windows(snap)
            for r in reports.values():
                assert len(r.sections) == 6
        finally:
            h.HISTORY_FILE = orig


# ─────────────────────────────────────────────────────────────────
# Read-only / non-mutation
# ─────────────────────────────────────────────────────────────────


class TestNoMutation:
    def test_report_build_does_not_alter_history(self, tmp_path):
        """Building the report must not write to history.jsonl."""
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h_f = tmp_path / "history.jsonl"
        h.HISTORY_FILE = h_f

        try:
            # Reset before
            if h_f.exists():
                h_f.unlink()

            snap = _make_snap()
            before = h_f.exists()
            build_review_report(snap, window_days=7)
            after = h_f.exists()

            # No mutation: history file should still not exist (or be unchanged)
            assert before == after
        finally:
            h.HISTORY_FILE = orig

    def test_report_build_is_pure(self, tmp_path):
        """Calling build_review_report twice with same input yields structurally similar output."""
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _make_snap()
            r1 = build_review_report(snap, window_days=7)
            r2 = build_review_report(snap, window_days=7)
            assert r1.window_days == r2.window_days
            assert len(r1.sections) == len(r2.sections)
        finally:
            h.HISTORY_FILE = orig
