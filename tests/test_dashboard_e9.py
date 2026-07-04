"""
E9 Signal Overlay Tests.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily_xauusd_brief.dashboard_chart import (  # noqa: E402
    build_signal_markers,
    render_chart,
)


@pytest.fixture
def fake_history(tmp_path):
    """Create a synthetic history dir with candlestick + fusion JSONs."""
    hist = tmp_path / "history"
    (hist / "candlestick").mkdir(parents=True)
    (hist / "fusion").mkdir(parents=True)

    # 5 candlestick payloads: bullish -> bullish -> reversal bearish ->
    #                          bearish continuation -> reversal bullish
    payloads_cs = [
        {"timestamp": "2026-07-01T00:00:00Z", "bias": "bullish"},
        {"timestamp": "2026-07-02T00:00:00Z", "bias": "bullish"},
        {"timestamp": "2026-07-03T00:00:00Z", "bias": "bearish"},
        {"timestamp": "2026-07-04T00:00:00Z", "bias": "bearish"},
        {"timestamp": "2026-07-05T00:00:00Z", "bias": "bullish"},
    ]
    for i, p in enumerate(payloads_cs):
        (hist / "candlestick" / f"2026-07-0{i+1}_candlestick.json").write_text(
            json.dumps(p), encoding="utf-8"
        )

    # 2 fusion payloads: one with conflict, one consistent
    payloads_fus = [
        {"timestamp": "2026-07-02T00:00:00Z", "conflict_label": "missing_briefing"},
        {"timestamp": "2026-07-04T00:00:00Z", "conflict_label": "conflict_clear"},
    ]
    for i, p in enumerate(payloads_fus):
        (hist / "fusion" / f"2026-07-0{i+1}_fus.json").write_text(
            json.dumps(p), encoding="utf-8"
        )

    return hist


def test_returns_empty_list_when_no_history(tmp_path):
    """No candlestick or fusion files → empty markers list."""
    empty_dir = tmp_path / "history"
    (empty_dir / "candlestick").mkdir(parents=True)
    (empty_dir / "fusion").mkdir(parents=True)
    markers = build_signal_markers(history_dir=empty_dir)
    assert markers == []


def test_markers_are_sorted_by_time_ascending(fake_history):
    """Markers array is chronological (ascending time)."""
    markers = build_signal_markers(history_dir=fake_history)
    times = [m["time"] for m in markers]
    assert times == sorted(times), "markers must be sorted ascending"


def test_all_four_marker_types_emitted(fake_history):
    """4-shape coverage: LONG arrowUp, SHORT/range, REV arrowUpDown, CONFLICT circle."""
    markers = build_signal_markers(history_dir=fake_history)
    shape_counts = {}
    for m in markers:
        shape_counts[m["shape"]] = shape_counts.get(m["shape"], 0) + 1
    assert shape_counts.get("arrowUp")      >= 2, f"need ≥2 LONG markers, got {shape_counts}"
    assert shape_counts.get("arrowDown")    >= 1, f"need ≥1 SHORT marker, got {shape_counts}"
    assert shape_counts.get("arrowUpDown")  >= 2, f"need ≥2 REVERSAL markers, got {shape_counts}"
    assert shape_counts.get("circle")       >= 1, f"need ≥1 CONFLICT marker, got {shape_counts}"


def test_reversal_color_is_amber(fake_history):
    """Reversal markers use amber color (#f7b731)."""
    markers = build_signal_markers(history_dir=fake_history)
    rev_markers = [m for m in markers if m["shape"] == "arrowUpDown"]
    assert rev_markers, "expected at least one reversal marker"
    for m in rev_markers:
        assert m["color"] == "#f7b731"


def test_long_color_green_short_color_red(fake_history):
    """LONG = green #3fb950, SHORT = red #f85149."""
    markers = build_signal_markers(history_dir=fake_history)
    long_markers = [m for m in markers if m["shape"] == "arrowUp"]
    short_markers = [m for m in markers if m["shape"] == "arrowDown"]
    assert long_markers and short_markers
    for m in long_markers:
        assert m["color"] == "#3fb950"
    for m in short_markers:
        assert m["color"] == "#f85149"


def test_conflict_marker_text_conf(fake_history):
    """Conflict markers carry text='CONF' and color purple."""
    markers = build_signal_markers(history_dir=fake_history)
    conf = [m for m in markers if m["text"] == "CONF"]
    assert conf, "expected at least one CONF marker (fusion conflict_label=missing_briefing)"
    for m in conf:
        assert m["color"] == "#9b59b6"
        assert m["position"] == "inBar"


def test_render_chart_backward_compat_no_markers():
    """markers=None / empty produces HTML with empty markerData array."""
    html_out = render_chart([{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2}])
    # Empty markers by default
    assert '"markerData"' in html_out or "markerData = []" in html_out
    assert "setMarkers(" in html_out  # JS branch always emits, but guarded


def test_render_chart_injects_non_empty_markers(fake_history):
    """Non_empty markers flow into HTML as a JSON array."""
    markers = build_signal_markers(history_dir=fake_history)
    bars = [{"time": t, "open": 1, "high": 2, "low": 1, "close": 2} for t in [m["time"] for m in markers]]
    html_out = render_chart(bars, markers=markers)
    assert "LONG" in html_out or "SHORT" in html_out or "REV" in html_out or "CONF" in html_out


def test_render_chart_ignores_position_for_marker_text(tmp_path):
    """All 4 shapes must be valid Lightweight Charts marker shapes."""
    valid_shapes = {"arrowUp", "arrowDown", "arrowUpDown", "circle"}
    hist = tmp_path / "history"
    (hist / "candlestick").mkdir(parents=True)
    (hist / "fusion").mkdir(parents=True)
    (hist / "candlestick" / "_t_candlestick.json").write_text(
        json.dumps({"timestamp": "2026-07-01T00:00:00Z", "bias": "bullish"}),
        encoding="utf-8",
    )
    markers = build_signal_markers(history_dir=hist)
    assert markers
    for m in markers:
        assert m["shape"] in valid_shapes
