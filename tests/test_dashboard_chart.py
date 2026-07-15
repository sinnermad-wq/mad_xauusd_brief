"""
E4 Chart Foundation — Dashboard Chart Tests.

Verifies:
  1. build_chart_bar_json reads existing candlestick history schema
  2. generate_historical_bars produces deterministic mock data
  3. render_chart produces valid Lightweight Charts HTML
  4. All timeframes have correct bar counts and structure
  5. None / missing data degrades gracefully (no crash)

Does NOT:
  - Require a browser (HTML template is structural-verified only)
  - Test signal overlay (E4 scope exclusion)
  - Test polling / refresh (E4 scope exclusion)
  - Modify any engine or fusion logic
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

import sys
_helpers_path = Path(__file__).parent.parent / "src" / "daily_xauusd_brief" / "dashboard_chart.py"
import importlib.util
_spec = importlib.util.spec_from_file_location("dashboard_chart", _helpers_path)
_dc = importlib.util.module_from_spec(_spec)  # type: ignore[attr-defined]
_spec.loader.exec_module(_dc)  # type: ignore[union-attr]

build_chart_bar_json     = _dc.build_chart_bar_json
render_chart             = _dc.render_chart
generate_historical_bars  = _dc.generate_historical_bars
TIMEFRAMES               = _dc.TIMEFRAMES
DEFAULT_TIMEFRAME        = _dc.DEFAULT_TIMEFRAME


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def real_candle_json() -> dict:
    """Latest on-disk candlestick history JSON (2026-06-28)."""
    path = Path(__file__).parent.parent / "data" / "history" / "candlestick" / "2026-06-28T13-18-12_cdff4e4a7138_candlestick.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def anchor_ohlc() -> dict:
    """Expected OHLC values from the real candle JSON anchor bar."""
    return {
        "close":  4080.90794,
        "high":   4081.00668,
        "low":    4080.76027,
        "open":   4080.82149,
    }


# ── Test: build_chart_bar_json ───────────────────────────────────────────────


class TestBuildChartBarJson:
    """Reads existing history schema without modifying it."""

    def test_returns_list_of_bars(self, real_candle_json: dict):
        """Returns a non-empty list when given valid candle JSON."""
        bars, bar_time = build_chart_bar_json(real_candle_json)
        assert isinstance(bars, list)
        assert len(bars) > 0

    def test_last_bar_close_matches_anchor(self, real_candle_json: dict, anchor_ohlc: dict):
        """Last bar's close matches the source JSON's close (anchor preserved)."""
        bars, _ = build_chart_bar_json(real_candle_json)
        assert abs(bars[-1]["close"] - anchor_ohlc["close"]) < 0.001

    def test_last_bar_high_is_max_of_ohlc(self, real_candle_json: dict, anchor_ohlc: dict):
        """Last bar's high ≥ all other OHLC fields."""
        bars, _ = build_chart_bar_json(real_candle_json)
        last = bars[-1]
        assert last["high"] >= last["close"]
        assert last["high"] >= last["open"]
        assert last["high"] >= last["low"]

    def test_last_bar_low_is_min_of_ohlc(self, real_candle_json: dict):
        """Last bar's low ≤ all other OHLC fields."""
        bars, _ = build_chart_bar_json(real_candle_json)
        last = bars[-1]
        assert last["low"] <= last["close"]
        assert last["low"] <= last["open"]
        assert last["low"] <= last["high"]

    def test_latest_bar_time_is_valid_iso_str(self, real_candle_json: dict):
        """Bar time string is a readable UTC timestamp."""
        _, bar_time = build_chart_bar_json(real_candle_json)
        assert "HKT" in bar_time or "2026" in bar_time
        assert bar_time != "—"

    def test_none_candle_returns_empty_and_dash(self):
        """None input returns ([], '—') — no crash."""
        bars, bar_time = build_chart_bar_json(None)
        assert bars == []
        assert bar_time == "—"

    def test_empty_dict_returns_empty_and_dash(self):
        """Empty dict returns ([], '—') — graceful degradation."""
        bars, bar_time = build_chart_bar_json({})
        assert bars == []
        assert bar_time == "—"

    def test_missing_ohlc_fields_returns_empty(self):
        """Candle JSON without open/high/low/close fields returns ([], '—')."""
        bars, bar_time = build_chart_bar_json({
            "source_payload": {"rsi_14": 65.0}   # no OHLC
        })
        assert bars == []
        assert bar_time == "—"

    def test_all_bars_have_required_keys(self, real_candle_json: dict):
        """Every bar has time, open, high, low, close — no missing keys."""
        bars, _ = build_chart_bar_json(real_candle_json)
        for i, bar in enumerate(bars):
            for key in ["time", "open", "high", "low", "close"]:
                assert key in bar, f"Bar {i} missing key: {key}"

    def test_bars_are_chronologically_sorted(self, real_candle_json: dict):
        """Bars are ascending by time (oldest first, newest last)."""
        bars, _ = build_chart_bar_json(real_candle_json)
        for i in range(len(bars) - 1):
            assert bars[i]["time"] < bars[i + 1]["time"], \
                f"Bar {i} time {bars[i]['time']} >= bar {i+1} time {bars[i+1]['time']}"


# ── Test: generate_historical_bars ───────────────────────────────────────────


class TestGenerateHistoricalBars:
    """Deterministic mock bar generation for chart demonstration."""

    def test_count_matches_timeframe_config(self):
        """Number of bars matches the timeframe configuration."""
        for tf, cfg in TIMEFRAMES.items():
            bars = generate_historical_bars(
                anchor_close=4000.0, anchor_high=4005.0,
                anchor_low=3995.0, anchor_open=4000.0,
                anchor_time_sec=2000000, timeframe=tf,
            )
            assert len(bars) == cfg["bars"], f"{tf}: expected {cfg['bars']}, got {len(bars)}"

    def test_anchor_bar_is_last(self):
        """The anchor (real) bar is always the last entry."""
        bars = generate_historical_bars(
            anchor_close=4080.90794, anchor_high=4081.00668,
            anchor_low=4080.76027, anchor_open=4080.82149,
            anchor_time_sec=2000000, timeframe="1D",
        )
        assert bars[-1]["close"] == 4080.90794

    def test_deterministic_per_timeframe(self):
        """Same inputs always produce same bars (deterministic, not random)."""
        kwargs = dict(
            anchor_close=5000.0, anchor_high=5010.0,
            anchor_low=4990.0, anchor_open=5000.0,
            anchor_time_sec=1900000,
        )
        tf = "5m"
        run1 = generate_historical_bars(**kwargs, timeframe=tf)
        run2 = generate_historical_bars(**kwargs, timeframe=tf)
        assert run1 == run2, "Historical bars are not deterministic"

    def test_different_timeframes_produce_different_bars(self):
        """Different timeframes produce different bar counts/volatility."""
        kwargs = dict(
            anchor_close=4080.0, anchor_high=4085.0,
            anchor_low=4075.0, anchor_open=4080.0,
            anchor_time_sec=2000000,
        )
        bars_1m  = generate_historical_bars(**kwargs, timeframe="1m")
        bars_1D  = generate_historical_bars(**kwargs, timeframe="1D")
        assert len(bars_1m) != len(bars_1D)
        # Higher timeframe → higher volatility
        assert bars_1D[-2]["close"] != bars_1m[-2]["close"]   # different noise

    def test_all_timeframes_in_TIMEFRAMES_work(self):
        """Every defined timeframe key produces valid bars without error."""
        kwargs = dict(
            anchor_close=4000.0, anchor_high=4005.0,
            anchor_low=3995.0, anchor_open=4000.0,
            anchor_time_sec=1800000,
        )
        for tf in TIMEFRAMES:
            bars = generate_historical_bars(**kwargs, timeframe=tf)
            assert len(bars) == TIMEFRAMES[tf]["bars"]
            assert all(k in b for k in ["time","open","high","low","close"] for b in bars)


# ── Test: render_chart ────────────────────────────────────────────────────────


class TestRenderChart:
    """TradingView Lightweight Charts HTML generation."""

    def test_renders_html_string(self, real_candle_json: dict):
        """Returns a non-empty HTML string."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        assert isinstance(html, str)
        assert len(html) > 1000

    def test_contains_lightweight_charts_cdn(self, real_candle_json: dict):
        """HTML includes the Lightweight Charts CDN URL."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        assert "cdn.jsdelivr.net/npm/lightweight-charts" in html

    def test_contains_candlestick_series_setup(self, real_candle_json: dict):
        """HTML calls addCandlestickSeries (Lightweight Charts API)."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        assert "addCandlestickSeries" in html

    def test_bar_data_injected_as_json(self, real_candle_json: dict):
        """OHLC bar data is embedded as valid JSON in the HTML."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        # Bar data is assigned to a JS variable before setData() call
        import re
        match = re.search(r"const\s+barData\s*=\s*\[", html)
        assert match, "const barData = [ not found in HTML — bar JSON not injected"
        # The variable is then passed to setData
        assert "candleSeries.setData(barData)" in html

    def test_empty_bars_renders_html(self):
        """Empty bar list produces valid HTML (shows empty chart, no crash)."""
        html = render_chart([])
        assert "addCandlestickSeries" in html
        assert "lightweight-charts" in html

    def test_html_has_no_unclosed_script_tags(self, real_candle_json: dict):
        """HTML script tags are properly balanced (basic check)."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        assert html.count("<script") == html.count("</script>")

    def test_chart_uses_dark_theme(self, real_candle_json: dict):
        """Chart background is dark (#131722) to match dashboard."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        assert "#131722" in html  # TradingView dark theme

    def test_up_down_colors_match_dashboard_theme(self, real_candle_json: dict):
        """Candlestick colours align with dashboard: green up (#3fb950), red down (#f85149)."""
        bars, _ = build_chart_bar_json(real_candle_json)
        html = render_chart(bars)
        assert "#3fb950" in html   # green/up
        assert "#f85149" in html   # red/down


# ── Test: timeframe configuration ─────────────────────────────────────────────


class TestTimeframeConfig:
    """TIMEFRAMES dict is correctly structured for E4 scope."""

    def test_all_required_timeframes_present(self):
        """1m / 5m / 15m / 1h are present (minimum E4 requirement)."""
        required = {"1m", "5m", "15m", "1h"}
        assert required.issubset(TIMEFRAMES.keys()), \
            f"Missing timeframes: {required - TIMEFRAMES.keys()}"

    def test_all_timeframes_have_seconds_key(self):
        """Every timeframe has a 'seconds' key for bar generation."""
        for tf, cfg in TIMEFRAMES.items():
            assert "seconds" in cfg, f"{tf} missing 'seconds'"
            assert cfg["seconds"] > 0

    def test_all_timeframes_have_bars_key(self):
        """Every timeframe has a 'bars' key for count control."""
        for tf, cfg in TIMEFRAMES.items():
            assert "bars" in cfg, f"{tf} missing 'bars'"
            assert cfg["bars"] > 0

    def test_bar_count_reasonable(self):
        """Bar count is between 30 and 500 (enough to fill chart, not excessive)."""
        for tf, cfg in TIMEFRAMES.items():
            assert 30 <= cfg["bars"] <= 500, f"{tf} bars {cfg['bars']} out of range"

    def test_default_timeframe_exists(self):
        """DEFAULT_TIMEFRAME references a key that exists in TIMEFRAMES."""
        assert DEFAULT_TIMEFRAME in TIMEFRAMES


# ── Test: real history JSON compatibility ────────────────────────────────────


class TestRealHistoryCandleSchema:
    """Verify that build_chart_bar_json works with all on-disk candle JSONs."""

    @pytest.fixture
    def all_candle_files(self):
        candle_dir = Path(__file__).parent.parent / "data" / "history" / "candlestick"
        return sorted(candle_dir.glob("*_candlestick.json"))

    def test_all_candle_files_load(self, all_candle_files):
        """Every on-disk candlestick JSON can be loaded without error."""
        for f in all_candle_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            bars, bar_time = build_chart_bar_json(data)
            assert isinstance(bars, list)
            assert bar_time != ""

    def test_latest_candle_has_valid_ohlc(self, all_candle_files):
        """Most recent candle file has all required OHLC fields."""
        latest = json.loads(all_candle_files[-1].read_text(encoding="utf-8"))
        p = latest.get("source_payload", {})
        for key in ["open", "high", "low", "close"]:
            assert key in p, f"Latest candle missing {key}"
            assert isinstance(p[key], (int, float)), f"Latest candle {key} not numeric"

    def test_latest_candle_produces_chart_html(self, all_candle_files):
        """Most recent candle file produces valid chart HTML."""
        latest = json.loads(all_candle_files[-1].read_text(encoding="utf-8"))
        bars, _ = build_chart_bar_json(latest)
        html = render_chart(bars)
        assert len(html) > 1000


# ── E6: Real bars via PollingMarketDataAdapter ─────────────────────────────────


class TestBuildChartBarJsonFromAdapter:
    """E6: build_chart_bar_json_from_adapter fetches real bars from an adapter."""

    @pytest.fixture
    def mock_adapter(self):
        """MockMarketDataAdapter seeded with 4080.90 anchor."""
        from pathlib import Path
        import sys, importlib.util
        _mkt = str(Path(__file__).parent.parent / "src" / "daily_xauusd_brief" / "market_data.py")
        _spec = importlib.util.spec_from_file_location("market_data", _mkt)
        _md = importlib.util.module_from_spec(_spec)
        sys.modules["market_data"] = _md
        _spec.loader.exec_module(_md)
        return _md.MockMarketDataAdapter(anchor_close=4080.90)

    def test_returns_non_empty_list_with_mock_adapter(self, mock_adapter):
        """Bars returned when adapter is seeded with data."""
        mock_adapter.refresh(timeframes=["1D"])
        bars, bar_time = _dc.build_chart_bar_json_from_adapter(mock_adapter, "1D", limit=60)
        assert isinstance(bars, list)
        assert len(bars) > 0
        assert bar_time != "—"

    def test_last_bar_close_matches_adapter_anchor(self, mock_adapter):
        """Last bar's close is in the right ballpark of the adapter's anchor price.

        Mock generates random walk; strict equality is not expected.
        Verify close is within 10% of anchor (reasonable for mock noise).
        """
        mock_adapter.refresh(timeframes=["1D"])
        bars, _ = _dc.build_chart_bar_json_from_adapter(mock_adapter, "1D", limit=60)
        last_close = bars[-1]["close"]
        assert 4080.90 * 0.90 < last_close < 4080.90 * 1.10, \
            f"Last bar close {last_close} is too far from anchor 4080.90"

    def test_none_adapter_returns_empty_no_crash(self):
        """None adapter returns ([], '—') gracefully — no exception."""
        bars, bar_time = _dc.build_chart_bar_json_from_adapter(None)
        assert bars == []
        assert bar_time == "—"

    def test_empty_store_returns_empty_no_crash(self, mock_adapter):
        """Adapter with no refresh() called returns ([], '—')."""
        bars, bar_time = _dc.build_chart_bar_json_from_adapter(mock_adapter)
        assert bars == []
        assert bar_time == "—"

    def test_adapter_unknown_timeframe_returns_empty(self, mock_adapter):
        """Unknown timeframe returns ([], '—')."""
        mock_adapter.refresh(timeframes=["1D"])
        bars, bar_time = _dc.build_chart_bar_json_from_adapter(mock_adapter, "999min")
        assert bars == []

    def test_all_bars_have_required_keys(self, mock_adapter):
        """Every bar has time, open, high, low, close — no missing keys."""
        mock_adapter.refresh(timeframes=["1h"])
        bars, _ = _dc.build_chart_bar_json_from_adapter(mock_adapter, "1h", limit=20)
        for bar in bars:
            for key in ["time", "open", "high", "low", "close"]:
                assert key in bar, f"Missing key: {key}"

    def test_bars_are_chronologically_sorted(self, mock_adapter):
        """Bars ascending by time (oldest first, newest last)."""
        mock_adapter.refresh(timeframes=["1D"])
        bars, _ = _dc.build_chart_bar_json_from_adapter(mock_adapter, "1D", limit=60)
        for i in range(len(bars) - 1):
            assert bars[i]["time"] < bars[i + 1]["time"], \
                f"Bar {i} time not sorted"

    def test_bar_time_str_is_valid_format(self, mock_adapter):
        """Bar time string matches 'YYYY-MM-DD HH:MM HKT' format."""
        mock_adapter.refresh(timeframes=["1h"])
        _, bar_time = _dc.build_chart_bar_json_from_adapter(mock_adapter, "1h", limit=10)
        # Format: "YYYY-MM-DD HH:MM HKT"
        assert "HKT" in bar_time
        assert len(bar_time) > 10

    def test_renders_valid_html_with_adapter_bars(self, mock_adapter):
        """Adapter bars produce valid Lightweight Charts HTML."""
        mock_adapter.refresh(timeframes=["1D"])
        bars, _ = _dc.build_chart_bar_json_from_adapter(mock_adapter, "1D", limit=30)
        html = _dc.render_chart(bars)
        assert "addCandlestickSeries" in html
        assert len(html) > 1000


class TestLoadLatestCandle:
    """load_latest_candle() reads the most recent on-disk candlestick JSON."""

    def test_load_latest_candle_returns_dict(self):
        """Returns a non-None dict when on-disk candles exist."""
        result = _dc.load_latest_candle()
        assert result is not None
        assert isinstance(result, dict)

    def test_load_latest_candle_has_ohlc_fields(self):
        """Returned dict has required OHLC fields."""
        candle = _dc.load_latest_candle()
        p = candle.get("source_payload", {})
        for key in ["open", "high", "low", "close"]:
            assert key in p, f"Missing {key}"

    def test_load_latest_candle_produces_chart(self):
        """Latest candle produces valid chart HTML via legacy path."""
        candle = _dc.load_latest_candle()
        bars, _ = _dc.build_chart_bar_json(candle)
        html = _dc.render_chart(bars)
        assert len(html) > 1000


class TestE6ScopeExclusions:
    """Verify E6 exclusions are respected (no auto-refresh, no signal overlay)."""

    def test_no_autorefresh_in_dashboard_chart_module(self):
        """dashboard_chart.py does not import st.autorefresh or polling loops."""
        from pathlib import Path
        src = Path(__file__).parent.parent / "src" / "daily_xauusd_brief" / "dashboard_chart.py"
        content = src.read_text(encoding="utf-8")
        assert "autorefresh" not in content
        assert "setInterval" not in content
        assert "setTimeout" not in content

    def test_no_signal_overlay_indicators_in_chart(self):
        """Chart HTML template has no signal line or marker indicators."""
        bars, _ = _dc.build_chart_bar_json({})
        html = _dc.render_chart(bars)
        # Signal overlay would use addLineSeries or addHistogramSeries
        assert "addLineSeries" not in html
        assert "addHistogramSeries" not in html
        # But addCandlestickSeries IS the chart series (correct)
        assert "addCandlestickSeries" in html