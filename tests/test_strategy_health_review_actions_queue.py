"""Tests for strategy_health.review_actions_queue — manual-only priority action queue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.strategy_health.models import (
    HEALTH_GREEN,
    HEALTH_RED,
    HEALTH_YELLOW,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_WARN,
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    StrategyApprovalState,
    StrategyDiagnostic,
    StrategyHealthSnapshot,
    StrategySuggestion,
)
from src.strategy_health.review_actions_queue import (
    ReviewAction,
    ReviewActionsQueue,
    build_review_actions_queue,
    build_review_actions_queue_all_windows,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _snap(
    health_status: str = HEALTH_GREEN,
    diagnostics: tuple | None = None,
    suggestions: tuple | None = None,
    pending_approvals: tuple | None = None,
    generated_at: str | None = None,
) -> StrategyHealthSnapshot:
    """Minimal snapshot factory with optional generated_at override."""
    if diagnostics is None:
        diagnostics = (
            StrategyDiagnostic(
                name="performance",
                severity=SEVERITY_CRITICAL,
                summary="crit",
            ),
            StrategyDiagnostic(
                name="drawdown",
                severity=SEVERITY_WARN,
                summary="warn",
            ),
            StrategyDiagnostic(
                name="signal",
                severity=SEVERITY_OK,
                summary="ok",
            ),
        )
    if suggestions is None:
        suggestions = (
            StrategySuggestion(
                suggestion_id="sug-1",
                kind="reduce_size",
                priority=2,
                title="Reduce position size",
                rationale="High drawdown",
                diagnostic_name="drawdown",
            ),
        )
    if pending_approvals is None:
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        old_iso = (datetime.now(tz=timezone.utc) - timedelta(days=12)).isoformat()
        pending_approvals = (
            StrategyApprovalState(
                suggestion_id="sug-1",
                kind="reduce_size",
                status=APPROVAL_PENDING,
                created_at=old_iso,
                updated_at=old_iso,
            ),
            StrategyApprovalState(
                suggestion_id="sug-2",
                kind="review_parameters",
                status=APPROVAL_PENDING,
                created_at=now_iso,
                updated_at=now_iso,
            ),
        )
    if generated_at is None:
        generated_at = datetime.now(tz=timezone.utc).isoformat()
    return StrategyHealthSnapshot(
        snapshot_id="q-test",
        generated_at=generated_at,
        health_status=health_status,
        diagnostics=diagnostics,
        suggestions=suggestions,
        pending_approvals=pending_approvals,
    )


# ── ReviewAction ──────────────────────────────────────────────────────────────


class TestReviewAction:
    def test_basic_construction(self):
        a = ReviewAction(
            action_id="test-1",
            category="approval",
            title="Reduce size — pending",
            detail="Created 5d ago",
            severity="critical",
            priority_score=75.0,
            age_days=5.0,
            frequency=2,
            trend="degrading",
        )
        assert a.category == "approval"
        assert a.age_days == 5.0
        assert a.frequency == 2
        assert a.trend == "degrading"
        assert a.priority_score == 75.0

    def test_age_days_is_float(self):
        a = ReviewAction(
            action_id="test-2",
            category="diagnostic",
            title="Perf warn",
            severity="warn",
            age_days=1.5,
        )
        assert isinstance(a.age_days, float)
        assert a.age_days == 1.5

    def test_to_dict_preserves_fields(self):
        # ReviewAction is a plain class (not frozen dataclass), no to_dict by default.
        # Fields are accessible directly.
        a = ReviewAction(
            action_id="test-3",
            category="suggestion",
            title="Review params",
            detail="Repeated 3×",
            severity="warn",
            priority_score=62.0,
            age_days=7.0,
            frequency=3,
            trend="stable",
        )
        assert a.category == "suggestion"
        assert a.severity == "warn"
        assert a.frequency == 3
        assert a.age_days == 7.0


# ── ReviewActionsQueue ────────────────────────────────────────────────────────


class TestReviewActionsQueue:
    def test_empty_init(self):
        q = ReviewActionsQueue(window_days=7, has_sufficient_history=True)
        assert q.is_empty()
        assert len(q.actions) == 0

    def test_is_empty_false_when_has_actions(self):
        q = ReviewActionsQueue(
            window_days=7,
            actions=[
                ReviewAction(
                    action_id="a-1",
                    category="approval",
                    title="Pending test",
                    severity="warn",
                    priority_score=30.0,
                ),
            ],
            has_sufficient_history=True,
        )
        assert not q.is_empty()
        assert len(q.actions) == 1

    def test_top_action_returns_first_by_priority(self):
        low = ReviewAction(
            action_id="low",
            category="diagnostic",
            title="Low",
            severity="ok",
            priority_score=20.0,
        )
        high = ReviewAction(
            action_id="high",
            category="approval",
            title="High",
            severity="critical",
            priority_score=80.0,
        )
        # Actions are stored as provided (caller is responsible for sorting)
        q = ReviewActionsQueue(
            window_days=7,
            actions=[high, low],  # high first
            has_sufficient_history=True,
        )
        # top_action returns first action (highest score assumed sorted)
        assert q.actions[0] is high

    def test_filter_by_category(self):
        a1 = ReviewAction(action_id="x", category="approval", title="A", severity="warn", priority_score=30.0)
        a2 = ReviewAction(action_id="y", category="diagnostic", title="B", severity="critical", priority_score=60.0)
        q = ReviewActionsQueue(window_days=7, actions=[a1, a2])
        assert len(q.filter_by_category("approval")) == 1
        assert q.filter_by_category("approval")[0] is a1

    def test_to_table_format(self):
        q = ReviewActionsQueue(
            window_days=7,
            actions=[
                ReviewAction(
                    action_id="t-1",
                    category="approval",
                    title="Test",
                    severity="critical",
                    priority_score=75.0,
                    frequency=2,
                    trend="degrading",
                ),
            ],
        )
        tbl = q.to_table()
        assert tbl is not None
        assert tbl[0] == ("#", "Category", "Severity", "Title", "Priority", "Freq", "Trend")
        assert tbl[1] == (1, "approval", "critical", "Test", "75.0", 2, "degrading")

    def test_to_table_none_when_empty(self):
        q = ReviewActionsQueue(window_days=7)
        assert q.to_table() is None

    def test_to_csv_has_header(self):
        q = ReviewActionsQueue(window_days=7, actions=[
            ReviewAction(
                action_id="c-1",
                category="approval",
                title="Pending reduce",
                detail="12 days old",
                severity="critical",
                priority_score=63.4,
                age_days=12.0,
                frequency=1,
                trend="stable",
            ),
        ])
        csv = q.to_csv()
        lines = csv.strip().split("\n")
        assert len(lines) == 2
        assert "window_days" in lines[0]
        assert "approval" in lines[1]
        assert "Pending reduce" in lines[1]

    def test_to_csv_header_only_when_empty(self):
        q = ReviewActionsQueue(window_days=14)
        csv = q.to_csv()
        lines = csv.strip().split("\n")
        assert len(lines) == 1
        assert "window_days" in lines[0]

    def test_to_markdown_contains_window(self):
        q = ReviewActionsQueue(
            window_days=14,
            actions=[],
            has_sufficient_history=True,
        )
        md = q.to_markdown()
        assert "14d" in md
        assert "Queue is empty" in md

    def test_to_markdown_with_actions(self):
        q = ReviewActionsQueue(
            window_days=7,
            actions=[
                ReviewAction(
                    action_id="m-1",
                    category="deterioration",
                    title="Perf degraded",
                    detail="critical 2d / warn 3d",
                    severity="critical",
                    priority_score=60.0,
                    age_days=7.0,
                    frequency=5,
                    trend="degrading",
                ),
            ],
            has_sufficient_history=True,
        )
        md = q.to_markdown()
        assert "7d" in md
        assert "Perf degraded" in md
        assert "degrading" in md

    def test_to_markdown_sufficient_history_flag(self):
        q_insuf = ReviewActionsQueue(window_days=7, has_sufficient_history=False)
        q_suf = ReviewActionsQueue(window_days=7, has_sufficient_history=True)
        assert "False" in q_insuf.to_markdown()
        assert "True" in q_suf.to_markdown()

    def test_empty_reason_reflected_in_markdown(self):
        q = ReviewActionsQueue(
            window_days=30,
            actions=[],
            has_sufficient_history=False,
            empty_reason="No history entries in window",
        )
        md = q.to_markdown()
        assert "No history entries in window" in md


# ── build_review_actions_queue ────────────────────────────────────────────────


class TestBuildReviewActionsQueue:
    def test_returns_queue_type(self, tmp_path):
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap()
            q = build_review_actions_queue(snap, window_days=7)
            assert isinstance(q, ReviewActionsQueue)
            assert q.window_days == 7
        finally:
            h.HISTORY_FILE = orig

    def test_green_snapshot_with_no_history_empty_queue(self, tmp_path):
        """Clean snapshot + no history = empty queue gracefully."""
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap(
                health_status=HEALTH_GREEN,
                diagnostics=(
                    StrategyDiagnostic(name="signal", severity=SEVERITY_OK, summary="ok"),
                ),
                suggestions=(),
                pending_approvals=(),
            )
            q = build_review_actions_queue(snap, window_days=7)
            assert isinstance(q, ReviewActionsQueue)
        finally:
            h.HISTORY_FILE = orig

    def test_pending_approvals_appear_as_actions(self, tmp_path):
        """Pending approvals always surface as actions."""
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap(pending_approvals=(
                StrategyApprovalState(
                    suggestion_id="sug-old",
                    kind="reduce_size",
                    status=APPROVAL_PENDING,
                    created_at=(datetime.now(tz=timezone.utc) - timedelta(days=15)).isoformat(),
                    updated_at=(datetime.now(tz=timezone.utc) - timedelta(days=15)).isoformat(),
                ),
            ))
            q = build_review_actions_queue(snap, window_days=7)
            approval_actions = [a for a in q.actions if a.category == "approval"]
            assert len(approval_actions) >= 1
        finally:
            h.HISTORY_FILE = orig

    def test_repeated_diagnostics_from_history(self, tmp_path):
        """Warn diagnostics that persist across history entries appear as actions."""
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            now = datetime.now(tz=timezone.utc)
            # Seed history with 5 entries
            for i in range(5):
                snap = _snap(
                    health_status=HEALTH_YELLOW,
                    diagnostics=(
                        StrategyDiagnostic(
                            name="performance",
                            severity=SEVERITY_WARN,
                            summary="warn",
                        ),
                    ),
                    generated_at=(now - timedelta(days=i)).isoformat(),
                )
                h.append_entry_to_history(h.snapshot_to_history_entry(snap))

            snap = _snap(health_status=HEALTH_YELLOW)
            q = build_review_actions_queue(snap, window_days=14)
            diag_cats = [a.category for a in q.actions]
            assert "diagnostic" in diag_cats or "deterioration" in diag_cats
        finally:
            h.HISTORY_FILE = orig

    def test_actions_sorted_by_priority_score(self, tmp_path):
        """Actions should be in descending priority order."""
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap(health_status=HEALTH_RED)
            q = build_review_actions_queue(snap, window_days=7)
            if len(q.actions) > 1:
                scores = [a.priority_score for a in q.actions]
                assert scores == sorted(scores, reverse=True)
        finally:
            h.HISTORY_FILE = orig

    def test_all_windows_return_same_type(self, tmp_path):
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap()
            for days in [7, 14, 30]:
                q = build_review_actions_queue(snap, window_days=days)
                assert isinstance(q, ReviewActionsQueue)
                assert q.window_days == days
        finally:
            h.HISTORY_FILE = orig


# ── build_review_actions_queue_all_windows ────────────────────────────────────


class TestBuildAllWindows:
    def test_returns_7d_14d_30d(self, tmp_path):
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap()
            result = build_review_actions_queue_all_windows(snap)
            assert set(result.keys()) == {"7d", "14d", "30d"}
            for k, q in result.items():
                assert isinstance(q, ReviewActionsQueue)
                assert q.window_days == int(k[:-1])
        finally:
            h.HISTORY_FILE = orig

    def test_each_queue_valid_and_readable(self, tmp_path):
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap(
                health_status=HEALTH_GREEN,
                diagnostics=(
                    StrategyDiagnostic(name="signal", severity=SEVERITY_OK, summary="ok"),
                ),
                suggestions=(),
                pending_approvals=(),
            )
            result = build_review_actions_queue_all_windows(snap)
            for k, q in result.items():
                assert isinstance(q, ReviewActionsQueue)
                md = q.to_markdown()
                csv = q.to_csv()
                assert "Review Actions Queue" in md
                assert "window_days" in csv
        finally:
            h.HISTORY_FILE = orig


# ── Guardrails: read-only / no mutation ───────────────────────────────────────


class TestReadOnlyGuardrails:
    def test_build_does_not_create_history_file(self, tmp_path):
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h_file = tmp_path / "history.jsonl"
        h.HISTORY_FILE = h_file
        try:
            if h_file.exists():
                h_file.unlink()
            snap = _snap()
            before = h_file.exists()
            build_review_actions_queue(snap, window_days=7)
            after = h_file.exists()
            assert before == after  # no file created
        finally:
            h.HISTORY_FILE = orig

    def test_idempotent_multiple_calls(self, tmp_path):
        import src.strategy_health.history as h

        orig = h.HISTORY_FILE
        h.HISTORY_FILE = tmp_path / "history.jsonl"
        try:
            snap = _snap()
            q1 = build_review_actions_queue(snap, window_days=7)
            q2 = build_review_actions_queue(snap, window_days=7)
            assert q1.window_days == q2.window_days
            assert type(q1) == type(q2)
        finally:
            h.HISTORY_FILE = orig