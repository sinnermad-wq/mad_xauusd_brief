"""
E4/E6 Chart Foundation — Streamlit rendering module.

E4 responsibilities:
  1. Read latest OHLC bar from existing candlestick history JSON
  2. Generate mock historical bars for chart demonstration
  3. Render TradingView Lightweight Charts via st.components.v1.html
  4. Support timeframe switching (1m / 5m / 15m / 1h / 4h / 1D)
  5. Display latest bar timestamp

E6 responsibilities (extends E4):
  6. Accept PollingMarketDataAdapter for real OHLC bars
  7. Replace mock data with real Twelve Data bars when adapter provided
  8. Active timeframe loads real bars; inactive timeframes use cache/lazy load

Does NOT (E4 + E6 exclusions):
  - Poll / refresh data automatically
  - Render signal overlays
  - Call any engine recompute
  - Write files
  - WebSocket (Phase 2)
  - Intra-bar latest-price update

Requires:
  streamlit  (used only when running inside Streamlit app)
  PollingMarketDataAdapter from market_data (optional; falls back to mock)
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Optional

# ── Lightweight Charts HTML template ────────────────────────────────────────
# CDN-hosted TradingView Lightweight Charts v4.
# Self-contained: no JS build step, no npm, works in st.components.v1.html.

_LIGHTWEIGHT_CDN = (
    "https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
)

_CHART_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #131722; overflow: hidden; }}
    #chart-container {{ width: 100vw; height: 60vh; position: relative; }}
    #chart {{ width: 100%; height: 100%; }}
    #legend {{
      position: absolute; top: 12px; left: 16px; z-index: 10;
      color: #d1d4dc; font-family: -apple-system,BlinkMacSystemFont,sans-serif;
      font-size: 13px; pointer-events: none;
    }}
    #timestamp {{
      position: absolute; top: 12px; right: 16px; z-index: 10;
      color: #787b86; font-family: monospace; font-size: 12px;
      pointer-events: none;
    }}
  </style>
</head>
<body>
<div id="chart-container">
  <div id="legend"></div>
  <div id="timestamp">bar: —</div>
  <div id="chart"></div>
</div>
<script src="{cdn}"></script>
<script>
  const chart = LightweightCharts.createChart(
    document.getElementById("chart"),
    {{
      layout: {{
        background: {{ type: "solid", color: "#131722" }},
        textColor: "#d1d4dc",
        fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif",
      }},
      grid: {{
        vertLines: {{ color: "#1e222d" }},
        horzLines: {{ color: "#1e222d" }},
      }},
      width: document.getElementById("chart").parentElement.clientWidth,
      height: document.getElementById("chart").parentElement.clientHeight,
      crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
      rightPriceScale: {{ borderColor: "#2a2e39" }},
      timeScale: {{ borderColor: "#2a2e39", timeVisible: true, secondsVisible: false }},
    }}
  );

  const candleSeries = chart.addCandlestickSeries({{
    upColor:          "#3fb950",
    downColor:        "#f85149",
    borderUpColor:    "#3fb950",
    borderDownColor:  "#f85149",
    wickUpColor:      "#3fb950",
    wickDownColor:    "#f85149",
  }});

  const barData = {bar_json};
  candleSeries.setData(barData);

  // E9: signal markers overlay (built from history + fusion payloads)
  const markerData = {markers_json};
  if (Array.isArray(markerData) && markerData.length > 0) {{
    const markers = markerData.map(m => ({{
      time:    m.time,
      position: m.position,
      color:   m.color,
      shape:   m.shape,
      text:    m.text,
    }}));
    candleSeries.setMarkers(markers);
  }}

  // E10: price lines overlay (entry / stop_loss / take_profit)
  const priceLineData = {price_lines_json};
  if (Array.isArray(priceLineData) && priceLineData.length > 0) {{
    priceLineData.forEach(line => {{
      candleSeries.createPriceLine({{
        price: line.price,
        color: line.color,
        lineWidth: line.lineWidth,
        lineStyle: line.lineStyle,   // 0=Solid, 1=Dotted, 2=Dashed, 3=LargeDashed
        axisLabelVisible: line.axisLabelVisible,
        title: line.title,
      }});
    }});
  }}

  // Update legend with live OHLC on crosshair move
  const legendEl = document.getElementById("legend");
  chart.subscribeCrosshairMove(param => {{
    if (!param.time || !param.seriesData) {{
      legendEl.textContent = "";
      return;
    }}
    const d = param.seriesData.get(candleSeries);
    if (!d) return;
    const op = d.open >= d.close ? "#f85149" : "#3fb950";
    legendEl.innerHTML =
      `<span style="color:${{op}}">O</span>` +
      ` ${{d.open.toFixed(2)}}  ` +
      `<span style="color:${{op}}">H</span>` +
      ` ${{d.high.toFixed(2)}}  ` +
      `<span style="color:${{op}}">L</span>` +
      ` ${{d.low.toFixed(2)}}  ` +
      `<span style="color:${{op}}">C</span>` +
      ` ${{d.close.toFixed(2)}}`;
  }});

  // Update timestamp badge
  if (barData.length > 0) {{
    const last = barData[barData.length - 1];
    document.getElementById("timestamp").textContent =
      "latest bar: " + new Date(last.time * 1000).toUTCString();
  }}

  chart.timeScale().fitContent();
  window.addEventListener("resize", () => {{
    chart.resize(
      document.getElementById("chart").parentElement.clientWidth,
      document.getElementById("chart").parentElement.clientHeight
    );
  }});
</script>
</body>
</html>"""


# ── Timeframe config ──────────────────────────────────────────────────────────

TIMEFRAMES = {
    "1m":  {"seconds": 60,    "bars": 200, "label": "1 分鐘", "volatility_pct": 0.0005},
    "5m":  {"seconds": 300,   "bars": 200, "label": "5 分鐘", "volatility_pct": 0.001},
    "15m": {"seconds": 900,   "bars": 200, "label": "15 分鐘","volatility_pct": 0.0015},
    "1h":  {"seconds": 3600,  "bars": 150, "label": "1 小時", "volatility_pct": 0.003},
    "4h":  {"seconds": 14400, "bars": 100, "label": "4 小時", "volatility_pct": 0.005},
    "1D":  {"seconds": 86400, "bars": 60,  "label": "日線",  "volatility_pct": 0.008},
}

DEFAULT_TIMEFRAME = "1D"


# ── OHLC data helpers ────────────────────────────────────────────────────────


def make_mock_bar(open_: float, high: float, low: float, close: float,
                 time_sec: int, volatility: float) -> dict:
    """Generate a single OHLC bar around given anchor values."""
    r = random.Random(time_sec)   # deterministic per timestamp
    delta = volatility * (close + abs(open_))
    o = close + r.uniform(-delta, delta)
    c = close + r.uniform(-delta, delta)
    h = max(o, c) + r.uniform(0, delta * 0.5)
    l = min(o, c) - r.uniform(0, delta * 0.5)
    return {
        "time": time_sec,
        "open":  round(o, 5),
        "high":  round(h, 5),
        "low":   round(l, 5),
        "close": round(c, 5),
    }


def generate_historical_bars(anchor_close: float, anchor_high: float,
                              anchor_low: float, anchor_open: float,
                              anchor_time_sec: int,
                              timeframe: str = "1D") -> list[dict]:
    """Generate N mock historical bars leading up to the anchor bar.

    Deterministic per timeframe + anchor time — same inputs always produce the
    same mock data, so the chart is stable across re-renders.
    """
    cfg = TIMEFRAMES.get(timeframe, TIMEFRAMES[DEFAULT_TIMEFRAME])
    bars: list[dict] = []
    for i in range(cfg["bars"] - 1):          # bar 0 is the anchor (latest)
        t = anchor_time_sec - (cfg["seconds"] * (cfg["bars"] - 1 - i))
        bars.append(make_mock_bar(
            open_=anchor_open,
            high=anchor_high,
            low=anchor_low,
            close=anchor_close,
            time_sec=t,
            volatility=cfg["volatility_pct"],
        ))
    # Append anchor bar (real data)
    bars.append({
        "time":  anchor_time_sec,
        "open":  round(anchor_open,  5),
        "high":  round(anchor_high,  5),
        "low":   round(anchor_low,   5),
        "close": round(anchor_close, 5),
    })
    return bars


# ── Phase 2A: Intraday price freshness ────────────────────────────────────────

def format_price_freshness(info: dict | None) -> str:
    """Format price freshness as a display string.

    Args:
        info: dict with {price, timestamp, fresh} from adapter.get_price_info().
              None if no adapter / no price available.
    Returns:
        Display string e.g. "⏱ intrabar: 14:32:05" or "⚠️ delayed: 14:31:42"
    """
    if info is None:
        return ""
    ts = info.get("timestamp")
    if ts is None:
        return ""
    label = "⏱ intrabar" if info.get("fresh", True) else "⚠️ delayed"
    # Convert UTC timestamp to HKT for display
    hkt_ts = ts.replace(tzinfo=timezone.utc).astimezone(
        __import__("datetime").timezone(datetime.now().astimezone().utcoffset())
    )
    return f"{label}: {hkt_ts.strftime('%H:%M:%S')} HKT"


# ── Public API ───────────────────────────────────────────────────────────────


def build_chart_bar_json(latest_candle: Optional[dict]) -> tuple[list[dict], str]:
    """Return (bars_json, latest_bar_time_str) for the chart.

    Uses the latest candlestick JSON's OHLC as the anchor bar.
    All other bars are deterministic mock data.

    Returns:
        bars:   list of {time, open, high, low, close} dicts (JSON-serialisable)
        latest_bar_time_str: ISO8601 string of the anchor bar, or "—" if no data
    """
    if not latest_candle:
        return [], "—"

    p = latest_candle.get("source_payload", {}) or {}
    close  = p.get("close")
    high   = p.get("high")
    low    = p.get("low")
    open_  = p.get("open")

    if not all(isinstance(x, (int, float)) for x in [close, high, low, open_]):
        return [], "—"

    ts_raw = latest_candle.get("timestamp", "")
    anchor_time_sec = int(datetime.fromisoformat(
        ts_raw.replace("Z", "+00:00")
    ).timestamp())

    latest_bar_str = datetime.fromisoformat(
        ts_raw.replace("Z", "+00:00")
    ).strftime("%Y-%m-%d %H:%M UTC")

    bars = generate_historical_bars(
        anchor_close=close,
        anchor_high=high,
        anchor_low=low,
        anchor_open=open_,
        anchor_time_sec=anchor_time_sec,
        timeframe=DEFAULT_TIMEFRAME,
    )
    return bars, latest_bar_str


def build_chart_bar_json_from_adapter(
    adapter,
    timeframe: str = DEFAULT_TIMEFRAME,
    limit: int = 60,
) -> tuple[list[dict], str]:
    """Return (bars_json, latest_bar_time_str) using real bars from a data adapter.

    E6 — replaces mock with real Twelve Data OHLC bars.

    Args:
        adapter:     PollingMarketDataAdapter or MockMarketDataAdapter instance.
                     Must have .get_bars(timeframe, limit) returning list[Candle].
        timeframe:   one of TIMEFRAMES keys (default: "1D")
        limit:       max bars to fetch (default: 60)

    Returns:
        bars:        list of {time, open, high, low, close} dicts (JSON-serialisable)
        latest_bar_time_str: ISO8601 string of the latest bar, or "—" if no data

    If adapter is None or adapter.get_bars() returns empty, returns ([], "—")
    gracefully (no crash, no exception).
    """
    import sys as _sys

    # Lazy import to avoid circular dependency; also safe for tests without adapter
    if adapter is None:
        return [], "—"

    try:
        bars_candle = adapter.get_bars(timeframe, limit=limit)
    except Exception:
        # Adapter not yet refreshed, store empty, or network error
        return [], "—"

    if not bars_candle:
        return [], "—"

    # Convert Candle objects → chart-compatible dicts
    bars: list[dict] = []
    for c in bars_candle:
        # Candle.datetime may be timezone-aware or naive
        dt = c.datetime
        time_sec = int(dt.timestamp())

        bar: dict = {
            "time":  time_sec,
            "open":  round(c.open,  5),
            "high":  round(c.high,  5),
            "low":   round(c.low,   5),
            "close": round(c.close, 5),
        }
        bars.append(bar)

    latest = bars_candle[-1]
    # Format: match the "YYYY-MM-DD HH:MM UTC" pattern used elsewhere
    dt_str = latest.datetime.strftime("%Y-%m-%d %H:%M UTC")
    return bars, dt_str


def load_latest_candle() -> Optional[dict]:
    """Load the most recent on-disk candlestick history JSON.

    Used as fallback when no PollingMarketDataAdapter is available.
    Returns None if no candle file exists.
    """
    import json as _json
    from pathlib import Path

    candle_dir = Path(__file__).parent.parent.parent / "data" / "history" / "candlestick"
    if not candle_dir.is_dir():
        return None

    files = sorted(candle_dir.glob("*_candlestick.json"))
    if not files:
        return None

    return _json.loads(files[-1].read_text(encoding="utf-8"))


def build_signal_markers(history_dir=None, fusion_dir=None) -> list[dict]:
    """Build Lightweight Charts markers from disk history + fusion payloads.

    Returns a list of marker dicts ready for `candleSeries.setMarkers()`.
    Each marker: {{time, position, color, shape, text}}.

    Marker classification (based on candlestick `bias` and fusion `conflict_label`):
      • LONG      → arrowUp    + "#3fb950"  aboveBar    "LONG"
      • SHORT     → arrowDown  + "#f85149"  belowBar    "SHORT"
      • REVERSAL  → arrowUpDown+ "#f7b731"  aboveBar    "REV"
      • CONFLICT  → circle     + "#9b59b6"  inBar       "CONF"

    Sequence reconstruction:
      1. Read all candlestick JSON files (newest 30, sorted)
      2. Read all fusion JSON files (newest 30, sorted)
      3. Merge + sort by timestamp ascending
      4. Walk through: each candlestick emits a bias marker; each fusion with
         conflict_label != "ok"/"consistent"/"conflict_clear" emits a conflict marker
         on top of the same timestamp
      5. Detect reversals (bias changed since previous candlestick) — upgrade
         current marker shape to arrowUpDown, color amber
    """
    from pathlib import Path as _P
    import json as _json

    if history_dir is None:
        history_dir = _P.home() / "projects" / "daily-xauusd-bot" / "data" / "history"
    if fusion_dir is None:
        fusion_dir = history_dir  # fusion files are inside the same history tree

    # Collect candlestick payloads (newest 30, ascending time order)
    cs_files = sorted((history_dir / "candlestick").glob("*_candlestick.json"))[-30:] \
        if (history_dir / "candlestick").exists() else []
    fus_files = sorted((history_dir / "fusion").glob("*.json"))[-30:] \
        if (history_dir / "fusion").exists() else []

    events: list[tuple[int, dict]] = []   # (time, info_dict)

    for fp in cs_files:
        try:
            d = _json.loads(fp.read_text(encoding="utf-8"))
            ts = d.get("timestamp", "")
            if not ts:
                continue
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            time_sec = int(dt.timestamp())
            bias = (d.get("bias") or "neutral").lower()
            events.append((time_sec, {"kind": "bias", "bias": bias, "src": "candlestick"}))
        except Exception:
            continue

    for fp in fus_files:
        try:
            d = _json.loads(fp.read_text(encoding="utf-8"))
            ts = d.get("timestamp", "")
            if not ts:
                continue
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            time_sec = int(dt.timestamp())
            conflict = (d.get("conflict_label") or "").lower()
            events.append((time_sec, {"kind": "conflict", "label": conflict, "src": "fusion"}))
        except Exception:
            continue

    if not events:
        return []

    # Sort by time ascending
    events.sort(key=lambda x: x[0])

    # Conversion helpers
    _LONG = ("arrowUp", "aboveBar", "#3fb950", "LONG")
    _SHORT = ("arrowDown", "belowBar", "#f85149", "SHORT")
    _REV_UP = ("arrowUpDown", "aboveBar", "#f7b731", "REV")
    _REV_DOWN = ("arrowUpDown", "belowBar", "#f7b731", "REV")
    _NEUTRAL = ("circle", "inBar", "#787b86", "—")
    _CONFLICT = ("circle", "inBar", "#9b59b6", "CONF")

    prev_bias: str | None = None
    markers: list[dict] = []

    for time_sec, info in events:
        if info["kind"] == "conflict":
            # Fusion conflict → circle marker on the same timestamp (above the bar)
            shape, position, color, text = _CONFLICT
            markers.append({
                "time": time_sec,
                "position": position,
                "color": color,
                "shape": shape,
                "text": text,
            })
            continue

        bias = info.get("bias", "neutral")
        if bias == prev_bias or prev_bias is None:
            # Same as last → continue the previous marker shape
            if bias == "bullish":
                shape, position, color, text = _LONG
            elif bias == "bearish":
                shape, position, color, text = _SHORT
            else:
                shape, position, color, text = _NEUTRAL
        else:
            # Bias flipped → reversal marker (amber up/down)
            if prev_bias == "bullish" and bias == "bearish":
                shape, position, color, text = _REV_DOWN
            elif prev_bias == "bearish" and bias == "bullish":
                shape, position, color, text = _REV_UP
            elif bias == "bullish":
                shape, position, color, text = _LONG
            elif bias == "bearish":
                shape, position, color, text = _SHORT
            else:
                shape, position, color, text = _NEUTRAL

        markers.append({
            "time": time_sec,
            "position": position,
            "color": color,
            "shape": shape,
            "text": text,
        })
        prev_bias = bias

    return markers


def build_active_price_lines(history_dir=None) -> list[dict]:
    """Build latest active signal's entry / stop_loss / take_profit price lines.

    Reads the most recent candlestick JSON; if `execution_intent` carries
    numeric entry/stop_loss/take_profit values, returns 3 price-line dicts ready
    for `series.createPriceLine()`.

    Returns empty list when:
      - No candlestick JSON found
      - Latest candlestick has no execution_intent
      - All 3 values are None / missing

    Lightweight Charts createPriceLine(...) shape:
      {{ price, color, lineWidth, lineStyle, axisLabelVisible, title }}

    Style mapping (E10 spec, fixed):
      • entry         → #2962ff  solid      lineWidth=2  title="ENTRY"
      • stop_loss     → #f85149  dashed     lineWidth=2  title="STOP"
      • take_profit   → #3fb950  solid      lineWidth=2  title="TP"
    """
    from pathlib import Path as _P
    import json as _json

    if history_dir is None:
        history_dir = _P.home() / "projects" / "daily-xauusd-bot" / "data" / "history"

    cs_dir = history_dir / "candlestick"
    if not cs_dir.exists():
        return []
    files = sorted(cs_dir.glob("*_candlestick.json"))
    if not files:
        return []
    try:
        d = _json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return []
    ei = d.get("execution_intent") or {}
    if not ei:
        return []

    entry = ei.get("entry_type")
    stop  = ei.get("stop_loss")
    tp    = ei.get("take_profit")
    decision = (ei.get("decision") or "").lower()

    lines: list[dict] = []

    def _to_float(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    entry_f = _to_float(entry)
    stop_f  = _to_float(stop)
    tp_f    = _to_float(tp)

    if entry_f is not None:
        lines.append({
            "price": entry_f,
            "color": "#2962ff",
            "lineWidth": 2,
            "lineStyle": 0,        # LightweightCharts LineStyle.Solid
            "axisLabelVisible": True,
            "title": f"ENTRY {decision.upper()}" if decision else "ENTRY",
        })
    if stop_f is not None:
        lines.append({
            "price": stop_f,
            "color": "#f85149",
            "lineWidth": 2,
            "lineStyle": 1,        # LightweightCharts LineStyle.Dashed
            "axisLabelVisible": True,
            "title": "STOP",
        })
    if tp_f is not None:
        lines.append({
            "price": tp_f,
            "color": "#3fb950",
            "lineWidth": 2,
            "lineStyle": 0,
            "axisLabelVisible": True,
            "title": "TP",
        })
    return lines


def render_chart(
    bars: list[dict],
    container_height: int = 500,
    markers: list[dict] | None = None,
    price_lines: list[dict] | None = None,
) -> str:
    """Return the Lightweight Charts HTML for use in st.components.v1.html.

    The HTML is fully self-contained — no JS variables needed from Python.
    `markers` (optional) — list of marker dicts (time, shape, position, color, text).
    `price_lines` (optional) — list of price-line dicts for `series.createPriceLine()`.
    Backward compatible: when markers / price_lines are None, the JS branches
    fall back to no-ops (same as E4/E6/E8 behavior).
    """
    import json
    bar_json = json.dumps(bars, separators=(",", ":"))
    markers_json = json.dumps(markers or [], separators=(",", ":"))
    price_lines_json = json.dumps(price_lines or [], separators=(",", ":"))
    return _CHART_HTML.format(
        cdn=_LIGHTWEIGHT_CDN,
        bar_json=bar_json,
        markers_json=markers_json,
        price_lines_json=price_lines_json,
    )


# ── Streamlit entry points ───────────────────────────────────────────────────
# Only imported when running inside a Streamlit app.


def render_streamlit_chart(
    latest_candle: Optional[dict],
    timeframe: str = DEFAULT_TIMEFRAME,
    height: int = 500,
    adapter=None,
    markers: list[dict] | None = None,
    price_lines: list[dict] | None = None,
    price_freshness: dict | None = None,  # Phase 2A: intrabar freshness info
) -> None:
    """Streamlit-native chart render with optional real data adapter + markers + price lines.

    E6 — when adapter is provided, fetches real Twelve Data bars.
         When adapter is None, uses legacy mock data (backward compatible).
    E9 — when markers is provided (call build_signal_markers() outside),
         the chart shows them via setMarkers(). When markers is None,
         the chart is unchanged (backward compatible with E4/E6/E7/E8).
    E10 — when price_lines is provided (call build_active_price_lines() outside),
         the chart shows them via createPriceLine(). When price_lines is None,
         the chart is unchanged (backward compatible with E4/E6/E7/E8/E9).

    Call with real adapter + all overlays:
        adapter = PollingMarketDataAdapter()
        adapter.refresh(timeframes=["1h"])
        markers = build_signal_markers()
        price_lines = build_active_price_lines()
        render_streamlit_chart(
            None, timeframe="1h", adapter=adapter,
            markers=markers, price_lines=price_lines,
        )

    Call with mock (E4 backward-compatible, no markers, no price lines):
        from daily_xauusd_brief.dashboard_chart import (
            render_streamlit_chart, load_latest_candle
        )
        candle = load_latest_candle()
        render_streamlit_chart(candle, timeframe="1D")
    """
    import streamlit as st
    from streamlit.components.v1 import html

    if adapter is not None:
        # E6: real bars path
        bars, bar_time = build_chart_bar_json_from_adapter(
            adapter, timeframe=timeframe, limit=TIMEFRAMES[timeframe]["bars"]
        )
        if not bars:
            st.info(
                f"No {TIMEFRAMES[timeframe]['label']} data available. "
                "Try refreshing the data adapter first."
            )
            return
        source_note = (
            f"📊 Real XAU/USD {TIMEFRAMES[timeframe]['label']} bars "
            f"from Twelve Data | {len(bars)} bars loaded"
            + (f" | 🟢 signals: {len(markers or [])}" if markers else "")
            + (f" | 📏 lines: {len(price_lines or [])}" if price_lines else "")
        )
    else:
        # E4 legacy: mock bars path
        bars, bar_time = build_chart_bar_json(latest_candle)
        if not bars:
            st.info("No candle data available. Run `--mode candlestick --dry-run` first.")
            return
        source_note = (
            "⚠️ Historical bars are simulated (real-time feed not connected). "
            "Timeframe: " + TIMEFRAMES.get(timeframe, {}).get("label", timeframe)
            + (f" | 🟢 signals: {len(markers or [])}" if markers else "")
            + (f" | 📏 lines: {len(price_lines or [])}" if price_lines else "")
        )

    html(
        render_chart(
            bars, container_height=height,
            markers=markers, price_lines=price_lines,
        ),
        height=height + 40,
    )
    # Phase 2A: intrabar freshness indicator at top-left
    freshness_str = format_price_freshness(price_freshness)
    if freshness_str:
        st.caption(freshness_str)
    st.caption(f"🕐 Latest bar: **{bar_time}** | Chart: TradingView Lightweight Charts")
    st.caption(source_note)