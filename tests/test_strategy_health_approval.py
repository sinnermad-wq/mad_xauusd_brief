"""Tests for approval store."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from src.strategy_health.models import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    StrategyApprovalState,
)
from src.strategy_health.approval import (
    diff_approvals,
    get_pending,
    load_approvals,
    resolve_approval,
    upsert_approval,
    write_approvals,
)


def test_load_approvals_missing_file():
    assert load_approvals("/nonexistent/path/approvals.json") == {}


def test_load_approvals_parses_valid(tmp_path):
    p = tmp_path / "approvals.json"
    p.write_text(json.dumps({"s1": {"suggestion_id": "s1", "status": APPROVAL_PENDING}}))
    assert "s1" in load_approvals(str(p))


def test_write_and_read_cycle(tmp_path):
    p = tmp_path / "a.json"
    data = {"s1": {"suggestion_id": "s1", "status": APPROVAL_APPROVED}}
    write_approvals(data, str(p))
    loaded = load_approvals(str(p))
    assert loaded["s1"]["status"] == APPROVAL_APPROVED


def test_upsert_approval_inserts_new(tmp_path):
    p = tmp_path / "a.json"
    state = StrategyApprovalState(
        suggestion_id="s-new", kind="keep_running",
        status=APPROVAL_PENDING,
        created_at="2026-07-09T00:00:00Z",
        updated_at="2026-07-09T00:00:00Z",
    )
    upsert_approval(state, str(p))
    loaded = load_approvals(str(p))
    assert "s-new" in loaded
    assert loaded["s-new"]["status"] == APPROVAL_PENDING


def test_upsert_approval_updates_existing(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({
        "s1": {"suggestion_id": "s1", "kind": "keep_running",
               "status": APPROVAL_PENDING,
               "created_at": "2026-07-09T00:00:00Z",
               "updated_at": "2026-07-09T00:00:00Z"}
    }))
    state = StrategyApprovalState(
        suggestion_id="s1", kind="keep_running",
        status=APPROVAL_APPROVED,
        created_at="2026-07-09T00:00:00Z",
        updated_at="2026-07-09T00:05:00Z",
    )
    upsert_approval(state, str(p))
    loaded = load_approvals(str(p))
    assert loaded["s1"]["status"] == APPROVAL_APPROVED


def test_get_pending_filters_correctly(tmp_path):
    p = tmp_path / "a.json"
    write_approvals({
        "s1": {"suggestion_id": "s1", "kind": "keep_running",
               "status": APPROVAL_PENDING,
               "created_at": "2026-07-09T00:00:00Z",
               "updated_at": "2026-07-09T00:00:00Z"},
        "s2": {"suggestion_id": "s2", "kind": "watch_only",
               "status": APPROVAL_APPROVED,
               "created_at": "2026-07-09T00:00:00Z",
               "updated_at": "2026-07-09T00:00:00Z"},
    }, str(p))
    pending = get_pending(str(p))
    assert len(pending) == 1
    assert pending[0].suggestion_id == "s1"


def test_resolve_approval_changes_status(tmp_path):
    p = tmp_path / "a.json"
    write_approvals({
        "s1": {"suggestion_id": "s1", "kind": "keep_running",
               "status": APPROVAL_PENDING,
               "created_at": "2026-07-09T00:00:00Z",
               "updated_at": "2026-07-09T00:00:00Z"},
    }, str(p))
    resolve_approval("s1", APPROVAL_REJECTED, note="No change needed", resolved_by="mad", path=str(p))
    loaded = load_approvals(str(p))
    assert loaded["s1"]["status"] == APPROVAL_REJECTED
    assert loaded["s1"]["note"] == "No change needed"


def test_resolve_approval_unknown_id_creates_record(tmp_path):
    p = tmp_path / "a.json"
    resolve_approval("s99", APPROVAL_APPROVED, resolved_by="mad", path=str(p))
    loaded = load_approvals(str(p))
    assert loaded["s99"]["status"] == APPROVAL_APPROVED


def test_resolve_approval_invalid_status_raises():
    with pytest.raises(ValueError):
        resolve_approval("s1", "invalid_status")


def test_diff_approvals_separates_new_and_pending(tmp_path):
    from src.strategy_health.models import StrategySuggestion
    sugs = tuple([
        StrategySuggestion(suggestion_id="s1", kind="keep_running",
                           priority=8, title="t", rationale="r"),
        StrategySuggestion(suggestion_id="s2", kind="watch_only",
                           priority=7, title="t", rationale="r"),
        StrategySuggestion(suggestion_id="s3", kind="pause_strategy",
                           priority=1, title="t", rationale="r"),
    ])
    existing = {
        "s1": {"suggestion_id": "s1", "kind": "keep_running",
               "status": APPROVAL_APPROVED,
               "created_at": "2026-07-09T00:00:00Z",
               "updated_at": "2026-07-09T00:00:00Z"},
        "s2": {"suggestion_id": "s2", "kind": "watch_only",
               "status": APPROVAL_PENDING,
               "created_at": "2026-07-09T00:00:00Z",
               "updated_at": "2026-07-09T00:00:00Z"},
    }
    new_sugs, pending = diff_approvals(sugs, existing)
    assert [s.suggestion_id for s in new_sugs] == ["s3"]
    assert [s.suggestion_id for s in pending] == ["s2"]