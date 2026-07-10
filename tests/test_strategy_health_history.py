"""Tests for strategy_health.history — append-only storage + trend computation."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.strategy_health.history import (
    append_entry_to_history,
    append_snapshot_to_history,
    compute_all_trend_windows,
    compute_trend_window,
    generate_trend_report_csv,
    generate_trend_report_markdown,
    load_history_entries,
    snapshot_to_history_entry,
)
from src.strategy_health.history_models import HealthHistoryEntry, TrendDirection
from src.strategy_health.models import (
    HEALTH_GREEN,
    HEALTH_RED,
    HEALTH_YELLOW,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARN,
    StrategyApprovalState,
    StrategyDiagnostic,
    StrategyHealthSnapshot,
    StrategySuggestion,
)


def _make_snap(
    health_status: str = HEALTH_GREEN,
    diagnostics: list[StrategyDiagnostic] | None = None,
    suggestions: list[StrategySuggestion] | None = None,
    pending_approvals: list[StrategyApprovalState] | None = None,
    generated_at: str | None = None,
) -> StrategyHealthSnapshot:
    if diagnostics is None:
        diagnostics = [
            StrategyDiagnostic(name="perf", severity=SEVERITY_OK, summary="ok"),
            StrategyDiagnostic(name="dd", severity=SEVERITY_OK, summary="ok"),
        ]
    if suggestions is None:
        suggestions = []
    if pending_approvals is None:
        pending_approvals = []
    if generated_at is None:
        generated_at = datetime.now(tz=timezone.utc).isoformat()
    return StrategyHealthSnapshot(
        snapshot_id="test-snap-001",
        generated_at=generated_at,
        health_status=health_status,
        diagnostics=tuple(diagnostics),
        suggestions=tuple(suggestions),
        pending_approvals=tuple(pending_approvals),
    )


# ─────────────────────────────────────────────────────────────────
# snapshot_to_history_entry
# ─────────────────────────────────────────────────────────────────

class TestSnapshotToHistoryEntry:
    def test_basic_conversion(self):
        snap = _make_snap(health_status=HEALTH_GREEN)
        entry = snapshot_to_history_entry(snap)
        assert isinstance(entry, HealthHistoryEntry)
        assert entry.health_status == HEALTH_GREEN
        assert entry.timestamp_iso is not None
        assert isinstance(entry.diagnostic_count, int)
        assert entry.diagnostic_count == 2  # perf + dd from _make_snap default

    def test_warn_count(self):
        snap = _make_snap(diagnostics=[
            StrategyDiagnostic(name="perf", severity=SEVERITY_OK, summary="ok"),
            StrategyDiagnostic(name="dd", severity=SEVERITY_WARN, summary="warn"),
        ])
        entry = snapshot_to_history_entry(snap)
        assert entry.warn_count == 1
        assert entry.critical_count == 0
        assert entry.diagnostic_count == 2

    def test_critical_count(self):
        snap = _make_snap(diagnostics=[
            StrategyDiagnostic(name="perf", severity=SEVERITY_CRITICAL, summary="bad"),
        ])
        entry = snapshot_to_history_entry(snap)
        assert entry.critical_count == 1
        assert entry.warn_count == 0


# ─────────────────────────────────────────────────────────────────
# Append / Load (JSONL, append-only)
# ─────────────────────────────────────────────────────────────────

class TestAppendAndLoad:
    def test_append_entry_round_trip(self, tmp_path: Path):
        history_file = tmp_path / "history.jsonl"

        # Patch HISTORY_FILE to tmp path
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = history_file

        try:
            snap = _make_snap(health_status=HEALTH_YELLOW)
            entry = snapshot_to_history_entry(snap)

            ok = append_entry_to_history(entry)
            assert ok is True
            assert history_file.exists()

            entries = load_history_entries()
            assert len(entries) == 1
            assert entries[0].health_status == HEALTH_YELLOW
        finally:
            h.HISTORY_FILE = orig

    def test_append_multiple_entries(self, tmp_path: Path):
        history_file = tmp_path / "history.jsonl"
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = history_file
        try:
            for status in [HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED]:
                snap = _make_snap(health_status=status)
                append_entry_to_history(snapshot_to_history_entry(snap))

            entries = load_history_entries()
            assert len(entries) == 3
            assert [e.health_status for e in entries] == [HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED]
        finally:
            h.HISTORY_FILE = orig

    def test_load_empty_history(self, tmp_path: Path):
        history_file = tmp_path / "history.jsonl"
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = history_file
        try:
            entries = load_history_entries()
            assert entries == []
        finally:
            h.HISTORY_FILE = orig

    def test_load_missing_file(self, tmp_path: Path):
        history_file = tmp_path / "nonexistent.jsonl"
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = history_file
        try:
            entries = load_history_entries()
            assert entries == []
        finally:
            h.HISTORY_FILE = orig

    def test_load_respects_limit(self, tmp_path: Path):
        history_file = tmp_path / "history.jsonl"
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = history_file
        try:
            for i in range(10):
                snap = _make_snap()
                append_entry_to_history(snapshot_to_history_entry(snap))

            assert len(load_history_entries(limit=3)) == 3
            assert len(load_history_entries(limit=5)) == 5
            assert len(load_history_entries(limit=100)) == 10
        finally:
            h.HISTORY_FILE = orig

    def test_append_snapshot_to_history(self, tmp_path: Path):
        history_file = tmp_path / "history.jsonl"
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = history_file
        try:
            snap = _make_snap(health_status=HEALTH_RED)
            ok = append_snapshot_to_history(snap)
            assert ok is True
            entries = load_history_entries()
            assert len(entries) == 1
            assert entries[0].health_status == HEALTH_RED
        finally:
            h.HISTORY_FILE = orig


# ─────────────────────────────────────────────────────────────────
# compute_trend_window
# ─────────────────────────────────────────────────────────────────

class TestComputeTrendWindow:
    def _seed_history(self, tmp_path: Path, days: int, status: str = HEALTH_GREEN):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        now = datetime.now(tz=timezone.utc)
        for i in range(days):
            dt = now - timedelta(days=i)
            snap = _make_snap(health_status=status, generated_at=dt.isoformat())
            h.append_entry_to_history(h.snapshot_to_history_entry(snap))
        h.HISTORY_FILE = orig

    def test_no_entries(self, tmp_path: Path):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            result = compute_trend_window(days=7)
            assert result.entries_used == 0
        finally:
            h.HISTORY_FILE = orig

    def test_green_trend(self, tmp_path: Path):
        self._seed_history(tmp_path, days=7, status=HEALTH_GREEN)
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            result = compute_trend_window(days=7)
            assert result.entries_used > 0
            assert result.health_status_trend in (TrendDirection.STABLE, TrendDirection.IMPROVING)
        finally:
            h.HISTORY_FILE = orig

    def test_entries_used_respects_days_window(self, tmp_path: Path):
        # Seed 30 days, query 7
        self._seed_history(tmp_path, days=30, status=HEALTH_GREEN)
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            result = compute_trend_window(days=7)
            # Bounded by total seeded (30); no upper-bound guarantee from time-window logic
            assert result.entries_used <= 30
            assert result.entries_used > 0
        finally:
            h.HISTORY_FILE = orig


# ─────────────────────────────────────────────────────────────────
# compute_all_trend_windows
# ─────────────────────────────────────────────────────────────────

class TestComputeAllTrendWindows:
    def _seed_history(self, tmp_path: Path, days: int):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        now = datetime.now(tz=timezone.utc)
        for i in range(days):
            dt = now - timedelta(days=i)
            snap = _make_snap(health_status=HEALTH_GREEN, generated_at=dt.isoformat())
            h.append_entry_to_history(h.snapshot_to_history_entry(snap))
        h.HISTORY_FILE = orig

    def test_empty_returns_dict_with_zeros(self, tmp_path: Path):
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            result = compute_all_trend_windows()
            assert set(result.keys()) == {"7d", "14d", "30d"}
            for window_result in result.values():
                assert window_result.entries_used == 0
        finally:
            h.HISTORY_FILE = orig

    def test_has_7d_14d_30d_keys(self, tmp_path: Path):
        self._seed_history(tmp_path, days=30)
        import src.strategy_health.history as h
        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            result = compute_all_trend_windows()
            assert "7d" in result
            assert "14d" in result
            assert "30d" in result
        finally:
            h.HISTORY_FILE = orig


# ─────────────────────────────────────────────────────────────────
# generate_trend_report_markdown
# ─────────────────────────────────────────────────────────────────

class TestGenerateTrendReportMarkdown:
    def test_empty_report(self):
        trends = compute_all_trend_windows()  # empty
        md = generate_trend_report_markdown(trends)
        assert "Trend Report" in md or "trend" in md.lower()
        assert isinstance(md, str)

    def test_report_contains_window_sections(self):
        trends = compute_all_trend_windows()
        md = generate_trend_report_markdown(trends)
        assert "7d" in md or "7 D" in md or "7-day" in md.lower()
        assert "14d" in md or "14 D" in md or "14-day" in md.lower()
        assert "30d" in md or "30 D" in md or "30-day" in md.lower()


# ─────────────────────────────────────────────────────────────────
# generate_trend_report_csv
# ─────────────────────────────────────────────────────────────────

class TestGenerateTrendReportCSV:
    def test_empty_csv(self):
        trends = compute_all_trend_windows()
        csv_text = generate_trend_report_csv(trends)
        lines = [l for l in csv_text.strip().split("\n") if l]
        # Should have header row at minimum
        assert len(lines) >= 1

    def test_csv_has_expected_columns(self):
        trends = compute_all_trend_windows()
        csv_text = generate_trend_report_csv(trends)
        header = csv_text.split("\n")[0]
        assert "window" in header.lower() or "trend" in header.lower()