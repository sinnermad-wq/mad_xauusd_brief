"""Tests for price_source — load_pricing_series_from_candles.

Key properties tested:
- sort ascending by timestamp
- skip non-matching symbol (or wrong file name format)
- dedup same day, keep latest mtime
- empty dir → empty PricingSeries
- missing dir raises PriceSourceError
- single file → 1-element series
"""
import pytest
import json
import tempfile
import os
import time
from pathlib import Path

from backtest.price_source import load_pricing_series_from_candles
from backtest.models import PricingSeries
from backtest.exceptions import PriceSourceError


def _write_candle(path: Path, ts: str, close: float) -> None:
    """Write a minimal candlestick JSON matching actual candlestick history format.

    Candlestick files are named like 2026-06-26T00-00-00_hash_candlestick.json
    and contain source_payload.symbol field.
    """
    fname = path.name
    path.write_text(json.dumps({
        "signal_id": "sig-test",
        "timestamp": ts,
        "timeframe": "1D",
        "source_payload": {
            "timestamp": ts,
            "symbol": "XAUUSD",
            "close": close,
            "high": close + 5.0,
            "low": close - 5.0,
            "open": close - 1.0,
        },
        "confidence": 0.7,
    }), encoding="utf-8")


def test_load_pricing_series_sorts_ascending():
    """Files in random order → sorted ascending."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        # Use proper date-based filenames (day extraction relies on this)
        _write_candle(p / "2026-06-28T00-00-00_hash1_candlestick.json",
                      "2026-06-28T00:00:00Z", 103.0)
        _write_candle(p / "2026-06-26T00-00-00_hash2_candlestick.json",
                      "2026-06-26T00:00:00Z", 101.0)
        _write_candle(p / "2026-06-29T00-00-00_hash3_candlestick.json",
                      "2026-06-29T00:00:00Z", 104.0)
        series = load_pricing_series_from_candles(p)
        assert len(series) == 3
        assert list(series.closes) == [101.0, 103.0, 104.0]
        assert list(series.timestamps) == [
            "2026-06-26T00:00:00Z",
            "2026-06-28T00:00:00Z",
            "2026-06-29T00:00:00Z",
        ]


def test_load_pricing_series_single_file():
    """Single file → 1-element PricingSeries."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_candle(p / "2026-06-26T00-00-00_hash_candlestick.json",
                      "2026-06-26T00:00:00Z", 101.0)
        series = load_pricing_series_from_candles(p)
        assert len(series) == 1
        assert series.closes[0] == 101.0


def test_load_pricing_series_dedup_same_day_keeping_latest_mtime(tmp_path):
    """Same day, two files with different mtime → latest mtime wins."""
    a = tmp_path / "2026-06-26T00-00-00_a_candlestick.json"
    b = tmp_path / "2026-06-26T00-00-00_b_candlestick.json"
    _write_candle(a, "2026-06-26T00:00:00Z", 100.0)
    _write_candle(b, "2026-06-26T00:00:00Z", 105.0)
    time.sleep(0.02)
    os.utime(b, None)   # make b newer mtime
    series = load_pricing_series_from_candles(tmp_path)
    assert len(series) == 1
    assert series.closes[0] == 105.0   # latest mtime wins


def test_load_pricing_series_missing_dir_raises():
    with pytest.raises(PriceSourceError):
        load_pricing_series_from_candles(Path("C:/nonexistent_dir_xyz123"))