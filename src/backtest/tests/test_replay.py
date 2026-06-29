"""Tests for replay — load_signals + walk_forward.

Key properties tested:
- No-future-leak: signals sorted ascending, exit uses forward index only
- limit: takes last N signals
- from/to_date: inclusive range filter
- trade_candidate filter
- empty dir: no raise, empty list returned
"""
import pytest
from pathlib import Path
import tempfile
import json

from backtest.replay import load_signals, walk_forward, _normalise_fusion, _normalise_candlestick
from backtest.models import PricingSeries


# ─── Helpers ─────────────────────────────────────────────────────────────
def _fake_fusion_file(path: Path, ts: str, signal_id: str, decision: str = "long",
                      confidence: float = 0.7, trade_candidate: bool = True) -> None:
    path.write_text(json.dumps({
        "signal_id": signal_id,
        "run_timestamp": ts,
        "timestamp": ts,
        "timeframe": "1D",
        "fusion_confidence": confidence,
        "trade_candidate": trade_candidate,
        "consensus_label": "aligned",
        "conflict_label": "none",
        "execution_intent": {"decision": decision},
        "data_quality_flag": "ok",
    }), encoding="utf-8")


def _fake_candle_file(path: Path, ts: str, signal_id: str,
                     decision: str = "long", confidence: float = 0.7) -> None:
    path.write_text(json.dumps({
        "signal_id": signal_id,
        "timestamp": ts,
        "timeframe": "1D",
        "confidence": confidence,
        "trade_eligible": True,
        "source_payload": {
            "timestamp": ts,
            "symbol": "XAU/USD",
            "close": 4000.0,
            "validation": {
                "timeframe_alignment": {"label": "aligned"},
                "cross_engine": {"label": "none"},
            },
        },
        "execution_intent": {"decision": decision},
        "data_quality_flag": "ok",
    }), encoding="utf-8")


# ─── load_signals ────────────────────────────────────────────────────────
def test_load_signals_sorts_ascending_by_timestamp():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _fake_fusion_file(p / "b.json", "2026-06-28T00:00:00Z", "sig-b")
        _fake_fusion_file(p / "a.json", "2026-06-26T00:00:00Z", "sig-a")
        _fake_fusion_file(p / "c.json", "2026-06-29T00:00:00Z", "sig-c")
        from backtest.models import ReplaySpec
        spec = ReplaySpec(sources=("fusion",))
        signals = load_signals(spec, Path(td))
        assert len(signals) == 3
        assert [s["timestamp"] for s in signals] == [
            "2026-06-26T00:00:00Z",
            "2026-06-28T00:00:00Z",
            "2026-06-29T00:00:00Z",
        ]


def test_load_signals_limit_takes_last_n():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        for i in range(5):
            _fake_fusion_file(p / f"s{i}.json",
                              f"2026-06-{26+i:02d}T00:00:00Z", f"sig-{i}")
        from backtest.models import ReplaySpec
        spec = ReplaySpec(sources=("fusion",), limit=3)
        signals = load_signals(spec, Path(td))
        assert len(signals) == 3
        # Last 3 by timestamp ascending = the 3 most recent
        assert [s["signal_id"] for s in signals] == ["sig-2", "sig-3", "sig-4"]


def test_load_signals_from_to_date_filter():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        for d in ["2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"]:
            _fake_fusion_file(p / f"{d}.json", f"{d}T00:00:00Z", d)
        from backtest.models import ReplaySpec
        spec = ReplaySpec(sources=("fusion",), from_date="2026-06-26", to_date="2026-06-27")
        signals = load_signals(spec, Path(td))
        assert len(signals) == 2
        assert all("2026-06-2" in s["timestamp"] for s in signals)


def test_load_signals_trade_candidate_filter():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _fake_fusion_file(p / "a.json", "2026-06-26T00:00:00Z", "sig-a", trade_candidate=True)
        _fake_fusion_file(p / "b.json", "2026-06-27T00:00:00Z", "sig-b", trade_candidate=False)
        from backtest.models import ReplaySpec
        spec = ReplaySpec(sources=("fusion",), require_trade_candidate=True)
        signals = load_signals(spec, Path(td))
        assert len(signals) == 1
        assert signals[0]["trade_candidate"] is True

        spec2 = ReplaySpec(sources=("fusion",), require_trade_candidate=False)
        signals2 = load_signals(spec2, Path(td))
        assert len(signals2) == 1
        assert signals2[0]["trade_candidate"] is False


def test_load_signals_missing_dir_yields_empty_not_error():
    from backtest.models import ReplaySpec
    spec = ReplaySpec(sources=("nonexistent_source",))
    signals = load_signals(spec, Path("C:/nonexistent_root"))
    assert signals == []


# ─── walk_forward ────────────────────────────────────────────────────────
def test_walk_forward_long_decision_correct():
    """Long entry at 100, exit at 110 → raw=+10%, signed=+10%, direction_correct=True."""
    signals = [{
        "signal_id": "sig-1",
        "timestamp": "2026-06-26T00:00:00Z",
        "decision": "long",
        "confidence": 0.7,
        "trade_candidate": True,
        "consensus_label": "aligned",
        "conflict_label": "none",
        "regime_tag": None,
        "data_quality": "ok",
    }]
    # 5 bars: closes at 100, 102, 105, 108, 110
    prices = PricingSeries(
        timestamps=("2026-06-26T00:00:00Z", "2026-06-27T00:00:00Z",
                    "2026-06-28T00:00:00Z", "2026-06-29T00:00:00Z",
                    "2026-06-30T00:00:00Z"),
        closes=(100.0, 102.0, 105.0, 108.0, 110.0),
    )
    results = list(walk_forward(signals, prices, horizons=(1, 3)))
    assert len(results) == 2  # 2 horizons

    # Horizon 1: entry 100 → exit 102 (+2%)
    sig, h, entry, exit_, reason = results[0]
    assert h == 1 and entry == 100.0 and exit_ == 102.0 and reason == "ok"

    # Horizon 3: entry 100 → exit 108 (+8%)
    sig, h, entry, exit_, reason = results[1]
    assert h == 3 and entry == 100.0 and exit_ == 108.0 and reason == "ok"


def test_walk_forward_short_decision_correct():
    """Short entry at 100, exit at 90 → raw=-10%, signed=+10%, direction_correct=True."""
    signals = [{
        "signal_id": "sig-1",
        "timestamp": "2026-06-26T00:00:00Z",
        "decision": "short",
        "confidence": 0.6,
        "trade_candidate": True,
        "consensus_label": "aligned",
        "conflict_label": "none",
        "regime_tag": None,
        "data_quality": "ok",
    }]
    prices = PricingSeries(
        timestamps=("2026-06-26T00:00:00Z", "2026-06-27T00:00:00Z",
                    "2026-06-28T00:00:00Z"),
        closes=(100.0, 95.0, 90.0),
    )
    results = list(walk_forward(signals, prices, horizons=(1, 2)))
    assert len(results) == 2

    # h=1: 100→95, raw=-5%, signed=+5% (short profits from fall)
    sig, h, entry, exit_, reason = results[0]
    assert h == 1 and entry == 100.0 and exit_ == 95.0

    # h=2: 100→90, raw=-10%, signed=+10%
    sig, h, entry, exit_, reason = results[1]
    assert h == 2 and entry == 100.0 and exit_ == 90.0


def test_walk_forward_out_of_window():
    """Signal at last bar; horizon=3 → out_of_window."""
    signals = [{
        "signal_id": "sig-1",
        "timestamp": "2026-06-28T00:00:00Z",   # index 3 of 5
        "decision": "long",
        "confidence": 0.7,
        "trade_candidate": True,
        "consensus_label": "aligned",
        "conflict_label": "none",
        "regime_tag": None,
        "data_quality": "ok",
    }]
    prices = PricingSeries(
        timestamps=("2026-06-24T00:00:00Z", "2026-06-25T00:00:00Z",
                    "2026-06-26T00:00:00Z", "2026-06-27T00:00:00Z",
                    "2026-06-28T00:00:00Z"),
        closes=(100.0, 101.0, 102.0, 103.0, 104.0),
    )
    results = list(walk_forward(signals, prices, horizons=(3,)))
    sig, h, entry, exit_, reason = results[0]
    assert h == 3
    assert entry == 104.0
    assert exit_ is None
    assert reason == "out_of_window"


def test_walk_forward_none_decision_skipped_by_default():
    signals = [{
        "signal_id": "sig-1",
        "timestamp": "2026-06-26T00:00:00Z",
        "decision": "none",
        "confidence": 0.0,
        "trade_candidate": False,
        "consensus_label": "",
        "conflict_label": "",
        "regime_tag": None,
        "data_quality": None,
    }]
    prices = PricingSeries(
        timestamps=("2026-06-26T00:00:00Z",),
        closes=(100.0,),
    )
    results = list(walk_forward(signals, prices, horizons=(1,)))
    assert len(results) == 1
    sig, h, entry, exit_, reason = results[0]
    assert reason == "out_of_window"  # single bar, no future bar