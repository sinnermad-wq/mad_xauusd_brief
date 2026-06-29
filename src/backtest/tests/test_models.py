"""Tests for backtest.models — Outcome / ReplaySpec / PricingSeries / Reports.

Targets ~12 tests focused on:
- frozen dataclass invariants
- ReplaySpec validation (negative/zero horizons, empty sources)
- Outcome serialization (to_dict)
- PricingSeries sorted-ascending invariant
- CalibrationBucket & CalibrationReport shape
"""
import pytest

from backtest.models import (
    ReplaySpec,
    Outcome,
    CalibrationBucket,
    CalibrationReport,
    BacktestRunSummary,
    PricingSeries,
    DECISION_LONG,
    DECISION_NONE,
    OUTCOME_OK,
    OUTCOME_OUT_OF_WINDOW,
    VERDICT_OK,
    VERDICT_INSUFFICIENT,
)


# ─── ReplaySpec validation ────────────────────────────────────────────────
def test_replay_spec_defaults_to_1_3_5_horizons():
    spec = ReplaySpec()
    assert spec.horizons == (1, 3, 5)
    assert spec.sources == ("fusion",)
    assert spec.from_date is None
    assert spec.to_date is None
    assert spec.limit is None
    assert spec.require_trade_candidate is None
    assert spec.include_none_in_trades is False


def test_replay_spec_frozen_blocks_mutation():
    spec = ReplaySpec()
    with pytest.raises(Exception):
        spec.limit = 10  # type: ignore[misc]


def test_replay_spec_rejects_zero_horizon():
    with pytest.raises(ValueError):
        ReplaySpec(horizons=(0, 1))


def test_replay_spec_rejects_empty_horizons():
    with pytest.raises(ValueError):
        ReplaySpec(horizons=())


def test_replay_spec_rejects_negative_horizon():
    with pytest.raises(ValueError):
        ReplaySpec(horizons=(1, -1))


def test_replay_spec_rejects_empty_sources():
    with pytest.raises(ValueError):
        ReplaySpec(sources=())


# ─── Outcome ──────────────────────────────────────────────────────────────
def test_outcome_to_dict_roundtrip():
    o = Outcome(
        signal_id="sig-1",
        signal_ts="2026-06-27T21:24:38Z",
        decision=DECISION_LONG,
        horizon_bars=1,
        entry_price=4080.9,
        exit_price=4090.0,
        raw_return=0.00223,
        signed_return=0.00223,
        direction_correct=True,
        move_abs=0.00223,
        confidence=0.7,
        trade_candidate=True,
        consensus_label="aligned",
        conflict_label="none",
        regime_tag=None,
        timeframe="1D",
        data_quality="ok",
        outcome_reason=OUTCOME_OK,
    )
    d = o.to_dict()
    assert d["signal_id"] == "sig-1"
    assert d["decision"] == "long"
    assert d["direction_correct"] is True
    assert d["outcome_reason"] == "ok"


def test_outcome_out_of_window_carries_reason():
    o = Outcome(
        signal_id="sig-2",
        signal_ts="2026-06-27T21:24:38Z",
        decision=DECISION_LONG,
        horizon_bars=5,
        entry_price=None,
        exit_price=None,
        raw_return=None,
        signed_return=None,
        direction_correct=None,
        move_abs=None,
        confidence=0.6,
        trade_candidate=True,
        consensus_label="aligned",
        conflict_label="none",
        regime_tag=None,
        timeframe="1D",
        data_quality="ok",
        outcome_reason=OUTCOME_OUT_OF_WINDOW,
    )
    assert o.entry_price is None
    assert o.outcome_reason == "out_of_window"


# ─── PricingSeries invariants ─────────────────────────────────────────────
def test_pricing_series_must_be_sorted_ascending():
    with pytest.raises(ValueError):
        PricingSeries(
            timestamps=("2026-06-28", "2026-06-27"),
            closes=(100.0, 99.0),
        )


def test_pricing_series_length_mismatch():
    with pytest.raises(ValueError):
        PricingSeries(
            timestamps=("2026-06-27",),
            closes=(100.0, 99.0),
        )


def test_pricing_series_single_point_allowed():
    s = PricingSeries(timestamps=("2026-06-27",), closes=(100.0,))
    assert len(s) == 1
    assert s.to_dict() == {
        "timestamps": ["2026-06-27"],
        "closes": [100.0],
        "index_desc": False,
    }


# ─── CalibrationBucket / CalibrationReport / Summary shape ───────────────
def test_calibration_bucket_serializes_cleanly():
    b = CalibrationBucket(
        lo=0.5, hi=0.6, n=10, n_long=6, n_short=2, n_none=2,
        hit_rate=0.6, avg_signed_return=0.001,
        avg_raw_return=0.0005, avg_confidence=0.55,
    )
    d = b.to_dict()
    assert d["lo"] == 0.5 and d["hi"] == 0.6
    assert d["n"] == 10
    assert d["hit_rate"] == 0.6


def test_calibration_report_nests_buckets_in_to_dict():
    b = CalibrationBucket(
        lo=0.0, hi=0.1, n=1, n_long=1, n_short=0, n_none=0,
        hit_rate=0.0, avg_signed_return=0.0,
        avg_raw_return=0.0, avg_confidence=0.05,
    )
    r = CalibrationReport(
        buckets=(b,),
        ece=0.05, brier=0.04,
        n_total=1, n_long=1, n_short=0, n_none=0,
        by_trade_candidate_hit_rate={True: 0.0, False: 0.0},
        by_consensus_hit_rate={"aligned": 0.0},
        by_conflict_hit_rate={"none": 0.0},
        verdict=VERDICT_INSUFFICIENT,
    )
    d = r.to_dict()
    assert d["verdict"] == "INSUFFICIENT_DATA"
    assert isinstance(d["buckets"], list) and len(d["buckets"]) == 1
    assert d["buckets"][0]["n"] == 1
