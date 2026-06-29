"""Tests for report — write_report output shapes.

Key properties tested:
- write_report returns 3 paths (outcomes, calibration, summary)
- dry_run: paths point to <dry-run:...> no I/O
- outcomes.json: list of dicts with expected keys
- summary.md: contains key sections and verdict text
- INSUFFICIENT_DATA verdict → summary says INSUFFICIENT_DATA
"""
import pytest
from pathlib import Path
import tempfile
import json

from backtest.models import (
    ReplaySpec, Outcome, BacktestRunSummary,
    DECISION_LONG, VERDICT_OK, VERDICT_INSUFFICIENT,
)
from backtest.models import CalibrationReport, CalibrationBucket
from backtest.report import write_report


def _spec():
    return ReplaySpec(horizons=(1, 3), sources=("fusion",))


def _cal_report(verdict=VERDICT_INSUFFICIENT, n=2):
    b = CalibrationBucket(
        lo=0.5, hi=0.6, n=n, n_long=1, n_short=1, n_none=0,
        hit_rate=0.5, avg_signed_return=0.0,
        avg_raw_return=0.0, avg_confidence=0.55,
    )
    return CalibrationReport(
        buckets=(b,),
        ece=0.1, brier=0.15,
        n_total=n, n_long=1, n_short=1, n_none=0,
        by_trade_candidate_hit_rate={True: 0.5, False: 0.5},
        by_consensus_hit_rate={"aligned": 0.5},
        by_conflict_hit_rate={"none": 0.5},
        verdict=verdict,
    )


def _outcome(decision=DECISION_LONG):
    return Outcome(
        signal_id="sig-test",
        signal_ts="2026-06-27T00:00:00Z",
        decision=decision,
        horizon_bars=1,
        entry_price=100.0,
        exit_price=102.0,
        raw_return=0.02,
        signed_return=0.02,
        direction_correct=True,
        move_abs=0.02,
        confidence=0.55,
        trade_candidate=True,
        consensus_label="aligned",
        conflict_label="none",
        regime_tag=None,
        timeframe="1D",
        data_quality="ok",
        outcome_reason="ok",
    )


def _summary(verdict=VERDICT_INSUFFICIENT, n_outcomes=2):
    spec = _spec()
    cal = _cal_report(verdict=verdict, n=n_outcomes)
    return BacktestRunSummary(
        spec=spec,
        n_signals_loaded=1,
        n_outcomes=n_outcomes,
        skipped=(("ok", n_outcomes),),
        horizon_stats={1: {"n": n_outcomes, "hit_rate": 0.5, "avg_signed_return": 0.01}},
        calibration=cal,
        verdict=verdict,
    )


def test_write_report_returns_three_paths(tmp_path):
    summary = _summary(VERDICT_INSUFFICIENT)
    outcomes = [_outcome()]
    paths = write_report(summary, outcomes, output_dir=tmp_path, dry_run=False)
    assert set(paths.keys()) == {"outcomes", "calibration", "summary"}


def test_outcomes_json_schema(tmp_path):
    summary = _summary(VERDICT_INSUFFICIENT)
    outcomes = [_outcome()]
    paths = write_report(summary, outcomes, output_dir=tmp_path, dry_run=False)
    data = json.loads(paths["outcomes"].read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    row = data[0]
    for key in ("signal_id", "decision", "horizon_bars", "outcome_reason",
                "confidence", "trade_candidate"):
        assert key in row


def test_calibration_json_schema(tmp_path):
    summary = _summary(VERDICT_INSUFFICIENT)
    outcomes = [_outcome()]
    paths = write_report(summary, outcomes, output_dir=tmp_path, dry_run=False)
    data = json.loads(paths["calibration"].read_text(encoding="utf-8"))
    for key in ("ece", "brier", "n_total", "buckets", "verdict",
                "by_trade_candidate_hit_rate", "by_consensus_hit_rate", "by_conflict_hit_rate"):
        assert key in data
    assert isinstance(data["buckets"], list)


def test_summary_md_contains_key_sections(tmp_path):
    summary = _summary(VERDICT_INSUFFICIENT)
    outcomes = [_outcome()]
    paths = write_report(summary, outcomes, output_dir=tmp_path, dry_run=False)
    text = paths["summary"].read_text(encoding="utf-8")
    assert "# XAUUSD Backtest Summary" in text
    assert "Signals loaded" in text
    assert "INSUFFICIENT_DATA" in text


def test_dry_run_does_not_write_files(tmp_path):
    summary = _summary()
    outcomes = [_outcome()]
    paths = write_report(summary, outcomes, output_dir=tmp_path, dry_run=True)
    # dry-run: files not written (paths point to <dry-run:...>)
    assert "<dry-run:" in str(paths["outcomes"])


def test_summary_md_contains_horizon_stats(tmp_path):
    summary = BacktestRunSummary(
        spec=_spec(),
        n_signals_loaded=1,
        n_outcomes=2,
        skipped=(("ok", 2),),
        horizon_stats={
            1: {"n": 2, "hit_rate": 0.5, "avg_signed_return": 0.01},
            3: {"n": 1, "hit_rate": 0.0,  "avg_signed_return": -0.02},
        },
        calibration=_cal_report(VERDICT_OK, n=3),
        verdict=VERDICT_OK,
    )
    outcomes = [_outcome()]
    paths = write_report(summary, outcomes, output_dir=tmp_path, dry_run=False)
    text = paths["summary"].read_text(encoding="utf-8")
    assert "1-bar" in text
    assert "3-bar" in text
    assert "ECE" in text
    assert "Brier" in text