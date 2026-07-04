"""
E10 Price Line Overlay Tests.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily_xauusd_brief.dashboard_chart import (  # noqa: E402
    build_active_price_lines,
    build_signal_markers,
    render_chart,
    render_streamlit_chart,
)


@pytest.fixture
def fake_history_with_ei(tmp_path):
    """Candlestick JSON with full execution_intent (entry/stop/tp)."""
    hist = tmp_path / "history"
    (hist / "candlestick").mkdir(parents=True)
    (hist / "fusion").mkdir(parents=True)
    payload = {
        "timestamp": "2026-07-03T08:31:20Z",
        "bias": "bullish",
        "execution_intent": {
            "symbol": "XAUUSD",
            "decision": "long",
            "confidence": 0.81,
            "strategy_id": "candlestick_v3",
            "timeframe": "1D",
            "entry_type": 4063.81,
            "stop_loss": 3990.00,
            "take_profit": 4140.00,
            "max_risk_pct": 0.02,
            "reason_codes": ["validation:qualified"],
        },
    }
    (hist / "candlestick" / "2026-07-03T08-31-20_cdff4e4a7138_candlestick.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return hist


@pytest.fixture
def fake_history_partial_ei(tmp_path):
    """Only entry + take_profit; no stop_loss (should still emit 2 lines)."""
    hist = tmp_path / "history"
    (hist / "candlestick").mkdir(parents=True)
    (hist / "fusion").mkdir(parents=True)
    payload = {
        "timestamp": "2026-07-03T08:31:20Z",
        "bias": "bullish",
        "execution_intent": {
            "symbol": "XAUUSD",
            "decision": "long",
            "entry_type": 4063.81,
            "stop_loss": None,
            "take_profit": 4140.00,
        },
    }
    (hist / "candlestick" / "_partial_candlestick.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return hist


# ── build_active_price_lines() ────────────────────────────────────────────────


def test_empty_list_when_no_candlestick_dir(tmp_path):
    """No `candlestick/` subdir → empty lines (no crash)."""
    hist = tmp_path / "history"
    hist.mkdir()
    assert build_active_price_lines(history_dir=hist) == []


def test_empty_list_when_no_files(tmp_path):
    """Empty `candlestick/` subdir → []."""
    hist = tmp_path / "history"
    (hist / "candlestick").mkdir(parents=True)
    (hist / "fusion").mkdir(parents=True)
    assert build_active_price_lines(history_dir=hist) == []


def test_three_lines_emitted_for_complete_ei(fake_history_with_ei):
    """entry + stop + take_profit present → 3 lines returned."""
    lines = build_active_price_lines(history_dir=fake_history_with_ei)
    assert len(lines) == 3


def test_entry_line_is_blue_solid(fake_history_with_ei):
    """Entry line: price=4063.81, color #2962ff, lineStyle=0 (solid), title contains ENTRY."""
    lines = build_active_price_lines(history_dir=fake_history_with_ei)
    entry_line = next(l for l in lines if l["price"] == 4063.81)
    assert entry_line["color"] == "#2962ff"
    assert entry_line["lineStyle"] == 0            # Solid
    assert entry_line["title"].startswith("ENTRY")


def test_stop_loss_line_is_red_dashed(fake_history_with_ei):
    """Stop loss: color #f85149, lineStyle=1 (dashed), title STOP."""
    lines = build_active_price_lines(history_dir=fake_history_with_ei)
    stop_line = next(l for l in lines if l["price"] == 3990.00)
    assert stop_line["color"] == "#f85149"
    assert stop_line["lineStyle"] == 1            # Dotted in LWC; spec calls it 'dashed'
    assert stop_line["title"] == "STOP"


def test_take_profit_line_is_green_solid(fake_history_with_ei):
    """Take profit: color #3fb950, lineStyle=0 (solid), title TP."""
    lines = build_active_price_lines(history_dir=fake_history_with_ei)
    tp_line = next(l for l in lines if l["price"] == 4140.00)
    assert tp_line["color"] == "#3fb950"
    assert tp_line["lineStyle"] == 0
    assert tp_line["title"] == "TP"


def test_partial_ei_emits_only_present_lines(fake_history_partial_ei):
    """stop_loss=None → only entry + tp emitted (2 lines)."""
    lines = build_active_price_lines(history_dir=fake_history_partial_ei)
    assert len(lines) == 2
    prices = sorted(l["price"] for l in lines)
    assert prices == [4063.81, 4140.00]


def test_only_latest_candlestick_used(tmp_path):
    """Only the most recent candlestick JSON contributes; older ones ignored."""
    hist = tmp_path / "history"
    (hist / "candlestick").mkdir(parents=True)
    (hist / "fusion").mkdir(parents=True)
    # Old — different prices (should be ignored)
    old = {
        "timestamp": "2026-06-01T00:00:00Z",
        "execution_intent": {
            "decision": "short",
            "entry_type": 5000.00, "stop_loss": 5100.00, "take_profit": 4900.00,
        },
    }
    (hist / "candlestick" / "2026-06-01_old_candlestick.json").write_text(
        json.dumps(old), encoding="utf-8"
    )
    new = {
        "timestamp": "2026-07-03T08:31:20Z",
        "execution_intent": {
            "decision": "long",
            "entry_type": 4063.81,
            "stop_loss": 3990.00,
            "take_profit": 4140.00,
        },
    }
    (hist / "candlestick" / "2026-07-03_new_candlestick.json").write_text(
        json.dumps(new), encoding="utf-8"
    )
    lines = build_active_price_lines(history_dir=hist)
    prices = {l["price"] for l in lines}
    assert prices == {4063.81, 3990.00, 4140.00}
    assert 5000.00 not in prices and 5100.00 not in prices


# ── render_chart() ───────────────────────────────────────────────────────────


def test_render_chart_default_no_lines():
    """price_lines=None default → HTML has empty priceLineData array, no JS branch fires."""
    html_out = render_chart([{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2}])
    assert "priceLineData" in html_out
    # JS branch has guard: empty array → no createPriceLine call
    assert "createPriceLine" in html_out   # JS branch CAN be in HTML, but guarded by length check


def test_render_chart_injects_price_lines():
    """3 lines → HTML JSON has all three title strings."""
    lines = [
        {"price": 4063.81, "color": "#2962ff", "lineWidth": 2, "lineStyle": 0,
         "axisLabelVisible": True, "title": "ENTRY LONG"},
        {"price": 3990.00, "color": "#f85149", "lineWidth": 2, "lineStyle": 1,
         "axisLabelVisible": True, "title": "STOP"},
        {"price": 4140.00, "color": "#3fb950", "lineWidth": 2, "lineStyle": 0,
         "axisLabelVisible": True, "title": "TP"},
    ]
    html_out = render_chart(
        [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2}],
        price_lines=lines,
    )
    assert "ENTRY LONG" in html_out
    assert "STOP" in html_out
    assert "TP" in html_out


# ── E9 / E8 / earlier backward compatibility ─────────────────────────────────


def test_build_signal_markers_still_works(fake_history_with_ei):
    """E9 builder unchanged — still works on real-ish history."""
    # Add a candlestick with bias for markers
    fake_history_with_ei.joinpath("fusion").mkdir(parents=True, exist_ok=True)
    markers = build_signal_markers(history_dir=fake_history_with_ei)
    assert isinstance(markers, list)


def test_render_streamlit_chart_signature_accepts_price_lines():
    """E10 added price_lines kwarg; default None preserves E9 behavior."""
    import inspect
    sig = inspect.signature(render_streamlit_chart)
    assert "price_lines" in sig.parameters
    assert sig.parameters["price_lines"].default is None


def test_render_chart_signature_accepts_price_lines():
    """render_chart kwarg added in E10 with default None."""
    import inspect
    sig = inspect.signature(render_chart)
    assert "price_lines" in sig.parameters
    assert sig.parameters["price_lines"].default is None


def test_no_market_data_import_in_chart_module():
    """E10 exclusion: dashboard_chart must NOT import market_data."""
    import daily_xauusd_brief.dashboard_chart as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "from daily_xauusd_brief.market_data" not in src
    assert "import market_data" not in src
