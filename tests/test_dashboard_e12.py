"""Phase 2A: Intraday price freshness — get_price_info() + format_price_freshness()."""
import sys
from pathlib import Path
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from daily_xauusd_brief.market_data import (
    PollingMarketDataAdapter, QuotaTracker, CandleStore, MarketDataError
)


def _make_adapter(client=None, price_cache=None):
    """Build a minimal adapter for unit testing without real API keys."""
    adapter = PollingMarketDataAdapter.__new__(PollingMarketDataAdapter)
    adapter._key = "test"
    adapter._quota = QuotaTracker()
    adapter._client = client or MagicMock()
    adapter._store = CandleStore()
    adapter._last_refresh = None
    adapter._price_cache = price_cache or {"price": None, "timestamp": None}
    return adapter


class TestGetPriceInfo:
    """Phase 2A: get_price_info() TTL cache behaviour."""

    def test_fresh_call_on_empty_cache(self):
        """First call (no cache) must call live API and return fresh=True."""
        mock_client = MagicMock()
        mock_client.get_price.return_value = 4049.40
        adapter = _make_adapter(client=mock_client)

        result = adapter.get_price_info(ttl_seconds=30)

        assert result["price"] == 4049.40
        assert result["fresh"] is True
        assert result["timestamp"] is not None
        mock_client.get_price.assert_called_once()

    def test_cached_within_ttl_skips_api(self):
        """Within 30s TTL, must return cached value without calling API."""
        cached_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        adapter = _make_adapter(
            client=MagicMock(),
            price_cache={"price": 4049.40, "timestamp": cached_ts},
        )

        result = adapter.get_price_info(ttl_seconds=30)

        assert result["price"] == 4049.40
        assert result["fresh"] is True
        adapter._client.get_price.assert_not_called()

    def test_stale_cache_triggers_live_fetch(self):
        """When TTL expired, must call live API and update cache."""
        cached_ts = datetime.now(timezone.utc) - timedelta(seconds=60)
        mock_client = MagicMock()
        mock_client.get_price.return_value = 4050.00
        adapter = _make_adapter(
            client=mock_client,
            price_cache={"price": 4049.40, "timestamp": cached_ts},
        )

        result = adapter.get_price_info(ttl_seconds=30)

        assert result["price"] == 4050.00  # new price
        assert result["fresh"] is True
        mock_client.get_price.assert_called_once()

    def test_api_failure_returns_stale_cache(self):
        """On API failure with stale cache, returns stale (fresh=False)."""
        cached_ts = datetime.now(timezone.utc) - timedelta(seconds=60)
        mock_client = MagicMock()
        mock_client.get_price.side_effect = MarketDataError("boom")
        adapter = _make_adapter(
            client=mock_client,
            price_cache={"price": 4049.40, "timestamp": cached_ts},
        )

        result = adapter.get_price_info(ttl_seconds=30)

        assert result["price"] == 4049.40
        assert result["fresh"] is False
        assert result["timestamp"] == cached_ts


class TestFormatPriceFreshness:
    """Phase 2A: format_price_freshness() helper."""

    def test_returns_intrabar_for_fresh(self):
        from daily_xauusd_brief.dashboard_chart import format_price_freshness
        now_utc = datetime.now(timezone.utc)
        info = {"price": 4049.40, "timestamp": now_utc, "fresh": True}
        result = format_price_freshness(info)
        assert "⏱ intrabar" in result
        assert "HKT" in result

    def test_returns_delayed_for_stale(self):
        from daily_xauusd_brief.dashboard_chart import format_price_freshness
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=60)
        info = {"price": 4049.40, "timestamp": old_ts, "fresh": False}
        result = format_price_freshness(info)
        assert "⚠️ delayed" in result

    def test_returns_empty_for_none(self):
        from daily_xauusd_brief.dashboard_chart import format_price_freshness
        assert format_price_freshness(None) == ""

    def test_returns_empty_for_missing_timestamp(self):
        from daily_xauusd_brief.dashboard_chart import format_price_freshness
        assert format_price_freshness({"price": 4049.40, "timestamp": None, "fresh": True}) == ""


class TestRenderStreamlitChartSignature:
    """Phase 2A: render_streamlit_chart accepts price_freshness param."""

    def test_accepts_price_freshness_kwarg(self):
        from daily_xauusd_brief.dashboard_chart import render_streamlit_chart
        import inspect
        sig = inspect.signature(render_streamlit_chart)
        assert "price_freshness" in sig.parameters

    def test_price_freshness_defaults_to_none(self):
        from daily_xauusd_brief.dashboard_chart import render_streamlit_chart
        import inspect
        sig = inspect.signature(render_streamlit_chart)
        param = sig.parameters["price_freshness"]
        assert param.default is None