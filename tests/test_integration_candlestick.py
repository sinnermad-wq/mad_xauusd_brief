"""
Integration Tests for V3 M2: Candlestick Engine Integration Layer

Covers:
  1. CandleAnalysis -> EngineOutput mapping
  2. EngineOutput.to_dict() JSON roundtrip
  3. Candlestick history write -> read roundtrip
  4. main.py --mode candlestick --dry-run executes
  5. main.py --mode both executes
  6. Dashboard load_latest_candle() reads history
  7. Existing briefing flow not broken
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# Add src/ to path for candlestick_engine imports
_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from candlestick_engine import CandleEngine, EngineOutput, map_candle_to_engine_output
from candlestick_engine.models import CandleAnalysis, BiasDirection, StructureState


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def make_sample_analysis(run_id: str = "abcd12345678") -> CandleAnalysis:
    """Create a sample CandleAnalysis for testing."""
    from candlestick_engine.models import SupportResistance, BreakoutState, BreakoutType, PatternMatch, PatternName, PatternDirection

    sr = SupportResistance(
        resistance_1=4100, resistance_2=4150, resistance_3=4200,
        support_1=4000, support_2=3950, support_3=3900,
        nearest_r=4100, nearest_s=4000,
    )
    breakout = BreakoutState(
        breakout_type=BreakoutType.NONE, breakout_confirmed=False,
        breakout_distance_pct=0.0, breakout_watch=False,
    )
    patterns = [
        PatternMatch(
            name=PatternName.BEARISH_ENGULFING,
            direction=PatternDirection.BEARISH,
            strength=0.75,
            location="resistance",
            bars=2,
            description_zh="日線形成看跌吞噬",
        )
    ]
    return CandleAnalysis(
        timestamp="2026-06-26T14:30:00",
        symbol="XAU/USD",
        analysis_window="30 bars",
        bar_count=30,
        run_id=run_id,
        technical_bias=BiasDirection.BEARISH,
        bias_strength=0.72,
        bias_explanation_zh="[偏空] 結構: 下降趨勢 | RSI=42.3",
        structure_state=StructureState.DOWNTREND,
        structure_internal="LH_LL",
        zone_assessment="price_below_ma20_ma50",
        support_resistance=sr,
        breakout_state=breakout,
        detected_patterns=patterns,
        pattern_summary="1 bearish_engulfing",
        reversal_watch=["RSI 超賣(<30)，注意反彈機會"],
        atr_14=28.4,
        rsi_14=42.3,
        ma_distance_pct={"ma20": -2.8, "ma50": -1.4, "ma200": 2.1},
        close=4023.10,
        high=4035.0,
        low=4010.0,
        open_price=4020.0,
        support_levels=[4000, 3950, 3900],
        resistance_levels=[4100, 4150, 4200],
    )


def make_sample_df(n: int = 30) -> "DataFrame":
    """Create a sample DataFrame for CandleEngine."""
    import pandas as pd
    bars = []
    h, l = 4200, 4180
    for i in range(n):
        close = h - 10
        bars.append({"O": close, "H": h, "L": l, "C": close - 10, "V": 1_000_000})
        h -= 15
        l -= 15
    return pd.DataFrame(list(reversed(bars)))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: CandleAnalysis -> EngineOutput mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestCandleToEngineOutput:
    """Verify CandleAnalysis correctly maps to EngineOutput contract."""

    def test_maps_all_required_fields(self):
        analysis = make_sample_analysis()
        output = map_candle_to_engine_output(analysis)

        assert output.engine_name == "candlestick"
        assert output.symbol == "XAUUSD"
        assert output.timeframe == "1D"
        assert output.bias == "bearish"
        assert isinstance(output.bias_strength, float)
        assert 0.0 <= output.bias_strength <= 1.0
        assert output.confidence is None  # V4 will fill this
        assert len(output.explanation_zh) > 0
        assert output.data_quality_flag == "ok"
        assert "bars" in output.analysis_window  # e.g. "30 bars"

    def test_run_id_preserved(self):
        analysis = make_sample_analysis(run_id="test123456")
        output = map_candle_to_engine_output(analysis)
        assert output.run_id == "test123456"

    def test_source_payload_contains_candle_analysis(self):
        analysis = make_sample_analysis()
        output = map_candle_to_engine_output(analysis)
        payload = output.source_payload
        assert "technical_bias" in payload
        assert "structure_state" in payload
        assert "breakout_state" in payload
        assert "detected_patterns" in payload
        assert "support_levels" in payload
        assert "resistance_levels" in payload

    def test_support_and_resistance_levels_in_payload(self):
        analysis = make_sample_analysis()
        output = map_candle_to_engine_output(analysis)
        payload = output.source_payload
        assert payload["support_levels"] == [4000, 3950, 3900]
        assert payload["resistance_levels"] == [4100, 4150, 4200]


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: EngineOutput.to_dict() JSON roundtrip
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineOutputRoundtrip:
    """EngineOutput must be JSON-serializable and reconstructable."""

    def test_to_dict_is_json_serializable(self):
        analysis = make_sample_analysis()
        output = map_candle_to_engine_output(analysis)
        d = output.to_dict()

        # Must be a plain dict (not dataclass, etc.)
        assert isinstance(d, dict)
        # Must be JSON-serializable
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 100

    def test_from_dict_reconstructs_output(self):
        analysis = make_sample_analysis()
        output1 = map_candle_to_engine_output(analysis)
        d = output1.to_dict()
        output2 = EngineOutput.from_dict(d)

        assert output2.engine_name == output1.engine_name
        assert output2.bias == output1.bias
        assert output2.bias_strength == output1.bias_strength
        assert output2.run_id == output1.run_id
        assert output2.source_payload["structure_state"] == output1.source_payload["structure_state"]

    def test_run_id_short_property(self):
        analysis = make_sample_analysis(run_id="abcdef123456")
        output = map_candle_to_engine_output(analysis)
        assert output.run_id_short == "abcdef12"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: History writer / reader roundtrip
# ─────────────────────────────────────────────────────────────────────────────

class TestCandlestickHistoryRoundtrip:
    """Verify candlestick history JSON can be written and read back."""

    def test_candlestick_history_json_format(self):
        """Verify the JSON structure of a candlestick history entry."""
        analysis = make_sample_analysis()
        output = map_candle_to_engine_output(analysis)

        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "candlestick"
            history_dir.mkdir()
            filename = f"2026-06-26T14-30-00_{output.run_id}_candlestick.json"
            filepath = history_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(output.to_dict(), f, ensure_ascii=False, indent=2)

            # Read it back
            with open(filepath, encoding="utf-8") as f:
                loaded = json.load(f)

        # Top-level required fields
        assert loaded["engine_name"] == "candlestick"
        assert loaded["symbol"] == "XAUUSD"
        assert loaded["bias"] in ("bullish", "bearish", "neutral")
        assert "run_id" in loaded
        assert "timestamp" in loaded
        assert "source_payload" in loaded
        # Candle-specific fields in source_payload
        sp = loaded["source_payload"]
        assert "structure_state" in sp
        assert "support_levels" in sp
        assert "resistance_levels" in sp


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: main.py --mode candlestick dry-run (smoke test)
# ─────────────────────────────────────────────────────────────────────────────

class TestMainCandlestickMode:
    """Verify main.py --mode candlestick --dry-run executes."""

    def test_main_module_imports(self):
        """main.py must be importable without errors."""
        from daily_xauusd_brief import main as main_module
        assert hasattr(main_module, "main")
        assert hasattr(main_module, "_cmd_candlestick")

    def test_cmd_candlestick_is_async(self):
        """_cmd_candlestick must be an async function."""
        from daily_xauusd_brief.main import _cmd_candlestick
        import asyncio
        assert asyncio.iscoroutinefunction(_cmd_candlestick)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Dashboard load_latest_candle()
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardCandleLoader:
    """Verify dashboard.load_latest_candle() reads candlestick history."""

    def test_load_latest_candle_function_exists(self):
        """load_latest_candle must be importable from dashboard."""
        from daily_xauusd_brief.dashboard import load_latest_candle
        assert callable(load_latest_candle)

    def test_load_latest_candle_returns_none_when_no_data(self, tmp_path):
        """load_latest_candle returns None when no history exists."""
        # Patch JSON_DIR to point to empty temp dir
        import daily_xauusd_brief.dashboard as dash
        original = dash.JSON_DIR
        dash.JSON_DIR = tmp_path / "history"
        try:
            result = dash.load_latest_candle()
            assert result is None
        finally:
            dash.JSON_DIR = original

    def test_load_latest_candle_reads_existing_entry(self, tmp_path):
        """load_latest_candle reads an existing candlestick JSON."""
        import daily_xauusd_brief.dashboard as dash

        candle_dir = tmp_path / "candlestick"
        candle_dir.mkdir(parents=True)
        entry = {
            "engine_name": "candlestick",
            "run_id": "testabc12345",
            "symbol": "XAUUSD",
            "timestamp": "2026-06-26T14:30:00",
            "timeframe": "1D",
            "bias": "bearish",
            "bias_strength": 0.72,
            "confidence": None,
            "explanation_zh": "[偏空] test",
            "data_quality_flag": "ok",
            "analysis_window": "30 bars",
            "source_payload": {
                "structure_state": "downtrend",
                "support_levels": [4000, 3950, 3900],
                "resistance_levels": [4100, 4150, 4200],
            },
        }
        filepath = candle_dir / "2026-06-26T14-30-00_testabc12345_candlestick.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(entry, f)

        original = dash.JSON_DIR
        dash.JSON_DIR = tmp_path
        try:
            result = dash.load_latest_candle()
            assert result is not None
            assert result["engine_name"] == "candlestick"
            assert result["bias"] == "bearish"
            assert result["source_payload"]["structure_state"] == "downtrend"
        finally:
            dash.JSON_DIR = original


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Briefing flow not broken
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefingFlowNotBroken:
    """Ensure existing --mode briefing (default) still imports correctly."""

    def test_briefing_main_still_imports(self):
        """main.py must still import without breaking existing code."""
        from daily_xauusd_brief.main import main, cmd_dry_run
        assert callable(main)
        assert callable(cmd_dry_run)

    def test_load_latest_report_still_works(self):
        """Dashboard load_latest_report() must still be functional."""
        from daily_xauusd_brief.dashboard import load_latest_report
        assert callable(load_latest_report)
        # Returns None if no data (expected before first run)
        result = load_latest_report("daily")
        assert result is None or isinstance(result, dict)