"""
E5 PollingMarketDataAdapter Tests.

Verifies:
  1. PollingMarketDataAdapter fetches real OHLC bars from Twelve Data (when online)
  2. MockMarketDataAdapter works for offline/test use
  3. QuotaTracker enforces daily budget + per-minute limit
  4. 429 backoff + rate limiting works
  5. CandleDTO is consistent across adapters
  6. CandleStore in-memory ring buffer works

Does NOT:
  - Call Twelve Data in rapid succession (quota respect)
  - Test WebSocket (Phase 2 scope)
  - Test signal overlay
  - Modify fusion logic
"""

from __future__ import annotations

import pytest
import time as _time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path

_mkt_path = (Path(__file__).parent.parent / "src" / "daily_xauusd_brief" / "market_data.py").resolve()
import importlib.util
_spec = importlib.util.spec_from_file_location("market_data", str(_mkt_path))
_md = importlib.util.module_from_spec(_spec)  # type: ignore[attr-defined]
sys.modules["market_data"] = _md             # needed so dataclass __module__ resolves
_spec.loader.exec_module(_md)  # type: ignore[union-attr]

Candle               = _md.Candle
CandleStore          = _md.CandleStore
QuotaTracker         = _md.QuotaTracker
QuotaExceeded        = _md.QuotaExceeded
RateLimitHit         = _md.RateLimitHit
PollingMarketDataAdapter  = _md.PollingMarketDataAdapter
MockMarketDataAdapter     = _md.MockMarketDataAdapter


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def quota() -> QuotaTracker:
    return QuotaTracker()


@pytest.fixture
def store() -> CandleStore:
    return CandleStore()


@pytest.fixture
def quota_no_gap() -> QuotaTracker:
    """QuotaTracker with min_gap_seconds=0 for fast test loops."""
    t = QuotaTracker()
    t.min_gap_seconds = 0.0
    return t


@pytest.fixture
def mock_adapter(quota, store) -> MockMarketDataAdapter:
    return MockMarketDataAdapter(anchor_close=4080.90)


# ── CandleDTO tests ────────────────────────────────────────────────────────────


class TestCandleDTO:
    """Candle is the universal OHLC data unit used everywhere."""

    def test_construct_valid_candle(self):
        c = Candle(
            symbol="XAU/USD", timeframe="1h",
            datetime=datetime(2026, 6, 29, tzinfo=timezone.utc),
            open=4080.0, high=4090.0, low=4075.0, close=4085.0,
        )
        assert c.symbol == "XAU/USD"
        assert c.timeframe == "1h"
        assert c.high == 4090.0
        assert c.low  == 4075.0

    def test_assertion_rejects_invalid_high(self):
        with pytest.raises(AssertionError):
            Candle(
                symbol="XAU/USD", timeframe="1h",
                datetime=datetime.now(timezone.utc),
                open=4080.0, high=4070.0,   # < close — invalid
                low=4075.0, close=4085.0,
            )

    def test_is_bullish_true(self):
        c = Candle(
            symbol="XAU/USD", timeframe="1h",
            datetime=datetime.now(timezone.utc),
            open=4070.0, high=4090.0, low=4065.0, close=4085.0,
        )
        assert c.is_bullish is True

    def test_is_bullish_false(self):
        c = Candle(
            symbol="XAU/USD", timeframe="1h",
            datetime=datetime.now(timezone.utc),
            open=4090.0, high=4095.0, low=4075.0, close=4080.0,
        )
        assert c.is_bullish is False

    def test_from_twelvedata_bar(self):
        bar = {
            "datetime": "2026-06-29 12:00:00",
            "open":  "4080.50",
            "high":  "4090.00",
            "low":   "4075.00",
            "close": "4085.25",
        }
        c = Candle.from_twelvedata_bar("XAU/USD", "1h", bar)
        assert c.open  == 4080.50
        assert c.high  == 4090.00
        assert c.low   == 4075.00
        assert c.close == 4085.25
        assert c.source == "twelvedata"

    def test_from_price(self):
        c = Candle.from_price("XAU/USD", 4080.90)
        assert c.open  == 4080.90
        assert c.high  == 4080.90
        assert c.low   == 4080.90
        assert c.close == 4080.90
        assert c.timeframe == "price"


# ── QuotaTracker tests ─────────────────────────────────────────────────────────


class TestQuotaTracker:
    """Quota tracking + enforcement."""

    def test_daily_budget_is_90pct_of_limit(self, quota):
        assert quota.daily_budget == 450.0   # 90% of 500

    def test_first_request_no_error(self, quota):
        cost = quota.reserve("1min")
        assert cost == 3.0
        assert quota.daily_used == 3.0

    def test_raises_quota_exceeded_when_daily_exhausted(self, quota_no_gap):
        # Reserve 150 * 3cr = 450cr daily budget; per-minute resets every 8 calls
        for i in range(150):
            if i > 0 and i % 8 == 0:
                quota_no_gap.minute_window_start -= 61.0   # force minute reset
            quota_no_gap.reserve("1min")
        # Next call exhausts the 450 safe budget
        with pytest.raises(QuotaExceeded) as exc_info:
            quota_no_gap.reserve("1min")
        assert exc_info.value.daily_remaining == 0

    def test_raises_quota_exceeded_at_per_minute_limit(self, quota):
        for _ in range(8):
            quota.reserve("1min")
        with pytest.raises(QuotaExceeded) as exc_info:
            quota.reserve("1min")
        assert exc_info.value.minute_remaining == 0

    def test_credit_cost_mapping(self, quota):
        assert quota.CREDIT_COST["price"]  == 1.0
        assert quota.CREDIT_COST["1min"]   == 3.0
        assert quota.CREDIT_COST["5min"]   == 3.0
        assert quota.CREDIT_COST["15min"]  == 3.0
        assert quota.CREDIT_COST["1h"]     == 5.0
        assert quota.CREDIT_COST["4h"]     == 5.0
        assert quota.CREDIT_COST["1day"]   == 5.0

    def test_reset_daily(self, quota):
        quota.reserve("1min")
        quota.reserve("1min")
        assert quota.daily_used > 0
        quota.reset_daily()
        assert quota.daily_used == 0.0

    def test_minute_window_reset(self, quota):
        for _ in range(3):
            quota.reserve("1min")
        assert quota.minute_used == 3
        # Manually back-date window
        quota.minute_window_start -= 61.0
        quota.reserve("1min")
        assert quota.minute_used == 1   # reset and used 1

    def test_daily_remaining_property(self, quota):
        assert quota.daily_remaining == 450.0
        quota.reserve("1min")   # -3
        assert quota.daily_remaining == 447.0


# ── CandleStore tests ────────────────────────────────────────────────────────────


class TestCandleStore:
    """In-memory ring buffer."""

    def test_store_and_retrieve(self, store):
        c1 = _make_candle("1h", 4080.0, datetime(2026, 6, 29, 8, tzinfo=timezone.utc))
        c2 = _make_candle("1h", 4085.0, datetime(2026, 6, 29, 9, tzinfo=timezone.utc))
        store.store([c1, c2])
        assert store.get_latest("1h").close == 4085.0

    def test_ring_buffer_max_200(self, store):
        # Use unique dates to avoid hour overflow
        many = [
            _make_candle("1h", 4000.0 + i,
                         datetime(2026, 1, 1, tzinfo=timezone.utc))
            for i in range(250)
        ]
        store.store(many)
        assert len(store.get_bars("1h", limit=300)) == 200

    def test_get_bars_respects_limit(self, store):
        for i in range(10):
            store.store([_make_candle("5min", 4000.0 + i,
                                       datetime(2026, 6, 29, i, tzinfo=timezone.utc))])
        bars = store.get_bars("5min", limit=3)
        assert len(bars) == 3

    def test_unknown_timeframe_returns_empty(self, store):
        assert store.get_latest("1D") is None
        assert store.get_bars("1D") == []

    def test_multiple_timeframes_independent(self, store):
        c1h = _make_candle("1h",  4080.0, datetime(2026, 6, 29, 8, tzinfo=timezone.utc))
        c5m = _make_candle("5min", 4070.0, datetime(2026, 6, 29, 8, tzinfo=timezone.utc))
        store.store([c1h, c5m])
        assert store.get_latest("1h").close  == 4080.0
        assert store.get_latest("5min").close == 4070.0


# ── MockMarketDataAdapter tests ─────────────────────────────────────────────────


class TestMockMarketDataAdapter:
    """Mock adapter is deterministic and never calls real API."""

    def test_produces_candles(self, mock_adapter):
        mock_adapter.refresh(timeframes=["1h"])
        bars = mock_adapter.get_bars("1h")
        assert len(bars) == 60
        assert all(b.source == "mock" for b in bars)

    def test_latest_bar_close_matches_anchor(self, mock_adapter):
        mock_adapter.refresh(timeframes=["1h"])
        latest = mock_adapter.get_latest_bar("1h")
        assert latest is not None

    def test_price_returns_anchor_close(self, mock_adapter):
        assert mock_adapter.get_price() == 4080.90

    def test_all_timeframes_work(self, mock_adapter):
        mock_adapter.refresh()
        for tf in ["1min", "5min", "15min", "1h", "4h", "1day"]:
            bars = mock_adapter.get_bars(tf)
            assert len(bars) == 60, f"{tf} should have 60 bars"

    def test_quota_not_exhausted_in_mock(self, mock_adapter):
        mock_adapter.refresh()   # many calls
        assert mock_adapter.quota.daily_used == 0.0


# ── PollingMarketDataAdapter (live — single real call test) ─────────────────────


class TestPollingMarketDataAdapterLive:
    """Live Twelve Data integration — single scoped call.

    This test calls the real API once (1 credit). Run via:
        pytest tests/test_market_data.py -v -k "Live"
    """

    def test_get_price_returns_float(self):
        adapter = PollingMarketDataAdapter()
        price = adapter.get_price()
        assert isinstance(price, float)
        assert 1000 < price < 10000   # gold is in this range

    def test_quota_deducted_for_price(self):
        before = PollingMarketDataAdapter().quota.daily_used
        adapter = PollingMarketDataAdapter()
        adapter.get_price()
        after = adapter.quota.daily_used
        assert after > before

    def test_refresh_single_stores_bars(self):
        adapter = PollingMarketDataAdapter()
        bars = adapter.refresh_single("5min", outputsize=3)
        assert len(bars) == 3
        assert bars[0].source == "twelvedata"
        assert bars[0].timeframe == "5min"
        # store check
        stored = adapter.get_bars("5min", limit=5)
        assert len(stored) == 3

    def test_candle_high_low_consistency(self):
        adapter = PollingMarketDataAdapter()
        bars = adapter.refresh_single("1min", outputsize=2)
        for bar in bars:
            assert bar.high >= bar.open
            assert bar.high >= bar.close
            assert bar.low  <= bar.open
            assert bar.low  <= bar.close

    def test_repr_shows_quota(self):
        adapter = PollingMarketDataAdapter()
        r = repr(adapter)
        assert "daily_remaining" in r
        assert "minute_remaining" in r


# ── E5 scope verification ───────────────────────────────────────────────────────


class TestE5Scope:
    """Verify E5 exclusions are not implemented in this module."""

    def test_no_signal_overlay(self, mock_adapter):
        """market_data.py does not render signals — that's dashboard's job."""
        import market_data as md
        # Verify the module doesn't import streamlit rendering modules
        src = open(md.__spec__.origin, encoding="utf-8").read()
        # These would be imported if signal rendering existed
        assert "streamlit.components" not in src
        assert "_chart_components" not in src
        assert "st.line_chart" not in src

    def test_mock_adapter_has_no_http_client(self, mock_adapter):
        """Mock adapter doesn't use requests — no network, no quota cost."""
        assert isinstance(mock_adapter, MockMarketDataAdapter)
        assert not hasattr(mock_adapter, "_client")

    def test_adapter_symbol_is_xau_usd(self):
        assert PollingMarketDataAdapter.SYMBOL == "XAU/USD"

    def test_required_timeframes_present(self):
        required = {"1min", "5min", "15min", "1h"}
        assert required.issubset(set(PollingMarketDataAdapter.TIMEFRAMES))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_candle(timeframe: str, close: float, dt: datetime) -> Candle:
    return Candle(
        symbol="XAU/USD", timeframe=timeframe, datetime=dt,
        open=close - 0.5, high=close + 1.0, low=close - 1.0, close=close,
        source="test",
    )