"""Tests for freshness diagnostic."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

from src.strategy_health.models import StrategyHealthConfig
from src.strategy_health.diagnostics.freshness import (
    _age_minutes,
    _state_for_age,
    compute_freshness_diagnostic,
)


def _cfg(**o):
    return StrategyHealthConfig(**o)


def test_state_for_age_brackets():
    assert _state_for_age(15, 90) == "ok"
    assert _state_for_age(120, 90) == "warn"
    assert _state_for_age(200, 90) == "critical"
    assert _state_for_age(None, 90) == "unknown"


def test_age_minutes_handles_missing():
    assert _age_minutes("/nonexistent/file.json", datetime.now(tz=timezone.utc)) is None


def test_age_minutes_positive_for_present_file(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
    os.utime(str(p), (past.timestamp(), past.timestamp()))
    age = _age_minutes(str(p), datetime.now(tz=timezone.utc))
    assert age is not None
    assert age > 0


def test_freshness_unknown_when_no_inputs():
    d = compute_freshness_diagnostic(
        fusion_history_path=None,
        backtest_report_path=None,
        candlestick_snapshot_path=None,
        merged_signals=None,
        cfg=_cfg(),
    )
    assert d.severity == "critical"
    assert "stale_or_missing_inputs" in d.reasons


def test_freshness_ok_with_recent_inputs(tmp_path):
    f = tmp_path / "fusion.json"
    f.write_text("[]")
    b = tmp_path / "backtest.json"
    b.write_text("{}")
    c = tmp_path / "candle.json"
    c.write_text("{}")
    d = compute_freshness_diagnostic(
        fusion_history_path=str(f),
        backtest_report_path=str(b),
        candlestick_snapshot_path=str(c),
        merged_signals=[{"a": 1}, {"a": 2}],
        cfg=_cfg(),
    )
    assert d.severity == "ok"
    assert d.metrics["sources"]["merged_signal_records"]["count"] == 2


def test_freshness_warn_with_stale_inputs(tmp_path):
    f = tmp_path / "fusion.json"
    f.write_text("[]")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=150)
    os.utime(str(f), (past.timestamp(), past.timestamp()))
    b = tmp_path / "backtest.json"
    b.write_text("{}")
    d = compute_freshness_diagnostic(
        fusion_history_path=str(f),
        backtest_report_path=str(b),
        candlestick_snapshot_path=None,
        merged_signals=[{"a": 1}],
        cfg=_cfg(fusion_history_ttl_minutes=90),
    )
    assert d.severity in {"warn", "critical"}


def test_freshness_critical_when_very_stale(tmp_path):
    f = tmp_path / "fusion.json"
    f.write_text("[]")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=400)
    os.utime(str(f), (past.timestamp(), past.timestamp()))
    d = compute_freshness_diagnostic(
        fusion_history_path=str(f),
        backtest_report_path=None,
        candlestick_snapshot_path=None,
        merged_signals=[{"a": 1}],
        cfg=_cfg(fusion_history_ttl_minutes=90),
    )
    assert d.severity == "critical"


def test_freshness_handles_dict_signals():
    d = compute_freshness_diagnostic(
        fusion_history_path=None,
        backtest_report_path=None,
        candlestick_snapshot_path=None,
        merged_signals={"signals": [{"a": 1}]},
        cfg=_cfg(),
    )
    # still critical (missing paths), but signal count is 1 not 0
    assert d.metrics["sources"]["merged_signal_records"]["count"] == 1
