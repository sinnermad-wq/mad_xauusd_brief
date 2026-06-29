"""
V3 M3 — Candlestick HTML Report

Renders an EngineOutput (plus 30-bar OHLCV) into a self-contained, dark-themed
HTML report at reports/candlestick/YYYY-MM-DD.html.

Layout:
  • Header (symbol, date, run_id)
  • Signal banner (bias + strength emoji)
  • SVG mini candlestick chart (last ~30 daily bars)
  • Tech snapshot (structure, RSI, ATR)
  • Patterns list
  • Support / Resistance table
  • Breakout status
  • Footer + disclaimer

Pure functions — no I/O at import. Tested in tests/test_candlestick_report.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .contract import EngineOutput


# ── Theme tokens (keep aligned with dashboard.py v7.2) ──────────────────────
BG = "#0d1117"
PANEL = "#161b22"
ACCENT = "#f0b90b"  # gold
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#3fb950"
RED = "#f85149"
NEUTRAL = "#8b949e"

BIAS_COLOR = {"bullish": GREEN, "bearish": RED, "neutral": NEUTRAL}
BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
STRUCTURE_EMOJI = {
    "uptrend": "📈",
    "downtrend": "📉",
    "range": "↔️",
    "transition": "🔄",
}


# ── Public API ──────────────────────────────────────────────────────────────


def render_candlestick_report(
    output: EngineOutput,
    bars: Sequence[dict],
    report_date: str | None = None,
) -> str:
    """Return full HTML string for the candlestick report.

    Args:
        output    : EngineOutput (source_payload contains full CandleAnalysis).
        bars      : OHLCV list, oldest-first (each dict has datetime/o/h/l/c/v).
        report_date: Optional YYYY-MM-DD header (defaults to timestamp[0:10]).

    Note: bar layout must match how CandleEngine consumes it; SVG takes last 30
    bars even if more are passed.
    """
    p = output.source_payload or {}
    header_date = report_date or (output.timestamp[:10] if output.timestamp else "—")
    bias_color = BIAS_COLOR.get(output.bias, NEUTRAL)
    bias_emoji = BIAS_EMOJI.get(output.bias, "⚪")
    strength_pct = f"{output.bias_strength:.0%}"
    conf_pct = (
        f"{output.confidence:.0%}" if output.confidence is not None else "—"
    )
    struct_emoji = STRUCTURE_EMOJI.get(p.get("structure_state", ""), "•")

    svg_chart = _render_ohlc_svg(bars[-30:]) if bars else _empty_chart_placeholder()

    patterns_html = _render_patterns(p.get("detected_patterns") or [])
    supports = p.get("support_levels") or []
    resists = p.get("resistance_levels") or []
    sr_html = _render_sr_table(supports, resists)
    breakout_html = _render_breakout(p.get("breakout_state") or {})

    last_close = bars[-1].get("close") if bars else None
    last_close_str = f"${last_close:,.2f}" if isinstance(last_close, (int, float)) else "—"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<title>XAUUSD Candlestick — {header_date}</title>
<style>
  body {{ background: {BG}; color: {TEXT}; font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 32px; }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  h1 {{ color: {ACCENT}; margin: 0 0 4px 0; font-size: 22px; }}
  .sub {{ color: {MUTED}; font-size: 12px; margin-bottom: 24px; }}
  .panel {{ background: {PANEL}; border: 1px solid #30363d; border-radius: 8px; padding: 18px; margin-bottom: 16px; }}
  .signal-banner {{ border-left: 4px solid {bias_color}; padding: 14px 16px; background: #1c2128; border-radius: 4px; margin-bottom: 20px; }}
  .signal-emoji {{ font-size: 28px; margin-right: 8px; }}
  .signal-label {{ font-size: 20px; font-weight: 700; color: {bias_color}; }}
  .signal-meta {{ color: {MUTED}; font-size: 13px; margin-top: 6px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }}
  .metric {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }}
  .metric-label {{ color: {MUTED}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric-value {{ color: {TEXT}; font-size: 18px; font-weight: 600; margin-top: 4px; }}
  svg.chart {{ width: 100%; height: 280px; background: #0d1117; border-radius: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #30363d; }}
  th {{ color: {MUTED}; font-weight: 500; }}
  td.s {{ color: {GREEN}; font-weight: 600; }}
  td.r {{ color: {RED}; font-weight: 600; }}
  ul {{ padding-left: 18px; margin: 8px 0; }}
  li {{ margin: 4px 0; line-height: 1.5; }}
  .footer {{ color: {MUTED}; font-size: 11px; margin-top: 32px; text-align: center; padding-top: 16px; border-top: 1px solid #30363d; }}
  .pill {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
  .pill-bull {{ background: rgba(63,185,80,0.15); color: {GREEN}; }}
  .pill-bear {{ background: rgba(248,81,73,0.15); color: {RED}; }}
  .pill-warn {{ background: rgba(240,185,11,0.15); color: {ACCENT}; }}
  .pill-info {{ background: rgba(139,148,158,0.15); color: {MUTED}; }}
</style>
</head>
<body>
<div class="wrap">

  <h1>🥇 XAU/USD Candlestick Analysis</h1>
  <div class="sub">
    {header_date} · 收盤 ${last_close_str} · run_id <code>{output.run_id}</code> · window: {output.analysis_window}
  </div>

  <div class="signal-banner">
    <span class="signal-emoji">{bias_emoji}</span>
    <span class="signal-label">{output.bias.upper()}</span>
    <span style="margin-left:14px;color:{MUTED};">strength {strength_pct} · confidence {conf_pct}</span>
    <div class="signal-meta">{struct_emoji} 結構: {p.get("structure_state", "—")}</div>
  </div>

  <div class="panel">
    <div class="metric-label" style="margin-bottom:8px;">📊 近 30 日 K 線</div>
    {svg_chart}
  </div>

  <div class="panel">
    <div class="grid">
      <div class="metric">
        <div class="metric-label">結構</div>
        <div class="metric-value">{struct_emoji} {p.get("structure_state", "—")}</div>
      </div>
      <div class="metric">
        <div class="metric-label">RSI(14)</div>
        <div class="metric-value">{_fmt_num(p.get("rsi_14"))}</div>
      </div>
      <div class="metric">
        <div class="metric-label">ATR(14)</div>
        <div class="metric-value">{_fmt_num(p.get("atr_14"))}</div>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="metric-label" style="margin-bottom:8px;">📊 偵測型態</div>
    {patterns_html}
  </div>

  <div class="panel">
    <div class="metric-label" style="margin-bottom:8px;">支撐 / 阻力位</div>
    {sr_html}
  </div>

  <div class="panel">
    <div class="metric-label" style="margin-bottom:8px;">🚀 突破狀態</div>
    {breakout_html}
  </div>

  {(_explanation_panel(output.explanation_zh))}

  <div class="footer">
    Generated by daily-xauusd-bot V3 M3 · candlestick_engine v3.3<br/>
    本報告為研究摘要，不構成投資建議。
  </div>

</div>
</body>
</html>
"""


def write_candlestick_report(
    output: EngineOutput,
    bars: Sequence[dict],
    out_dir: str | Path,
    report_date: str | None = None,
) -> Path:
    """Render the HTML and write to out_dir/YYYY-MM-DD.html.

    Returns the resolved output Path.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    header_date = report_date or (output.timestamp[:10] if output.timestamp else "")
    if not header_date:
        raise ValueError("report_date required when output has no timestamp")
    target = out_path / f"{header_date}.html"
    target.write_text(
        render_candlestick_report(output, bars, report_date=header_date),
        encoding="utf-8",
    )
    return target


# ── SVG chart ───────────────────────────────────────────────────────────────


def _render_ohlc_svg(bars: Sequence[dict]) -> str:
    """Render a simple candlestick SVG from OHLC bars.

    Fixed dimensions: width 100% × height 280px, drawn onto a viewBox 800×280.
    """
    if not bars:
        return _empty_chart_placeholder()

    w, h = 800, 280
    pad_l, pad_r, pad_t, pad_b = 40, 12, 16, 28
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    y_max, y_min = max(highs), min(lows)
    span = y_max - y_min or 1.0
    # pad y axis 5%
    y_max_p = y_max + span * 0.05
    y_min_p = y_min - span * 0.05
    span_p = y_max_p - y_min_p

    n = len(bars)
    slot = plot_w / n
    candle_w = max(2, slot * 0.6)

    def y(val: float) -> float:
        return pad_t + (1 - (val - y_min_p) / span_p) * plot_h

    parts: list[str] = []
    parts.append(
        f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
    )

    # background grid (5 horizontal lines)
    for i in range(5):
        gy = pad_t + (plot_h / 4) * i
        gyv = y_max_p - (y_max_p - y_min_p) * (i / 4)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w - pad_r}" y2="{gy:.1f}" '
            f'stroke="#30363d" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_l - 6}" y="{gy + 4:.1f}" fill="{MUTED}" '
            f'font-size="10" text-anchor="end">{gyv:,.0f}</text>'
        )

    # candles
    for idx, bar in enumerate(bars):
        o = float(bar["open"])
        c = float(bar["close"])
        hi = float(bar["high"])
        lo = float(bar["low"])
        cx = pad_l + slot * idx + slot / 2
        is_up = c >= o
        color = GREEN if is_up else RED
        # wick
        parts.append(
            f'<line x1="{cx:.1f}" y1="{y(hi):.1f}" '
            f'x2="{cx:.1f}" y2="{y(lo):.1f}" '
            f'stroke="{color}" stroke-width="1"/>'
        )
        # body
        y_o, y_c = y(o), y(c)
        body_top = min(y_o, y_c)
        body_h = max(1, abs(y_c - y_o))
        parts.append(
            f'<rect x="{cx - candle_w / 2:.1f}" y="{body_top:.1f}" '
            f'width="{candle_w:.1f}" height="{body_h:.1f}" '
            f'fill="{color}" stroke="{color}"/>'
        )

    # last close label
    last = bars[-1]
    parts.append(
        f'<text x="{w - pad_r - 4}" y="{y(float(last["close"])) - 4:.1f}" '
        f'fill="{ACCENT}" font-size="11" font-weight="700" text-anchor="end">'
        f'{float(last["close"]):,.2f}</text>'
    )

    # x labels (first / mid / last)
    def x_label(i: int, anchor: str) -> str:
        if i < 0 or i >= n:
            return ""
        dt = str(bars[i].get("datetime", ""))[:10]
        xpos = pad_l + slot * i + slot / 2
        return (
            f'<text x="{xpos:.1f}" y="{h - 8}" fill="{MUTED}" '
            f'font-size="10" text-anchor="{anchor}">{dt}</text>'
        )

    parts.append(x_label(0, "start"))
    parts.append(x_label(n // 2, "middle"))
    parts.append(x_label(n - 1, "end"))

    parts.append("</svg>")
    return "".join(parts)


def _empty_chart_placeholder() -> str:
    return (
        '<svg viewBox="0 0 800 280" style="width:100%;height:280px;'
        f'background:{BG};border-radius:4px;">'
        f'<text x="400" y="140" fill="{MUTED}" font-size="14" '
        'text-anchor="middle">無 K 線資料</text></svg>'
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _render_patterns(patterns: list[dict]) -> str:
    if not patterns:
        return '<div style="color:#8b949e;font-style:italic;">未偵測到顯著型態</div>'
    items = "".join(
        f"<li>{(p.get('description_zh') or p.get('name') or '?')}"
        f" <span style='color:#8b949e;font-size:12px;'>"
        f"({p.get('name', 'pattern')})</span></li>"
        for p in patterns[:5]
    )
    return f"<ul>{items}</ul>"


def _render_sr_table(supports: list, resists: list) -> str:
    rows = []
    max_len = max(len(supports), len(resists))
    for i in range(max_len):
        s = supports[i] if i < len(supports) else None
        r = resists[i] if i < len(resists) else None
        rows.append(
            f"<tr><td class='s'>{_fmt_price(s)}</td>"
            f"<td style='text-align:center;color:#8b949e;'>L{i + 1}</td>"
            f"<td class='r'>{_fmt_price(r)}</td></tr>"
        )
    if not rows:
        return (
            '<div style="color:#8b949e;font-style:italic;">'
            "未偵測到支撐 / 阻力位</div>"
        )
    return (
        "<table>"
        "<thead><tr><th>🟢 支撐 (Support)</th><th>層級</th>"
        "<th>🔴 阻力 (Resistance)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_breakout(bs: dict) -> str:
    if bs.get("breakout_confirmed"):
        direction = "向上" if bs.get("breakout_type", {}).get("value") == "break_up" else "向下"
        return (
            f"<span class='pill pill-bull'>已確認突破 {direction}</span> · "
            f"level {bs.get('breakout_level', '—')}"
        )
    if bs.get("breakout_watch"):
        lvl = bs.get("breakout_watch_level")
        lvl_str = f" @ {lvl:.1f}" if isinstance(lvl, (int, float)) else ""
        return f"<span class='pill pill-warn'>觀察中{lvl_str}</span>"
    return "<span class='pill pill-info'>未偵測突破訊號</span>"


def _explanation_panel(text: str) -> str:
    if not text:
        return ""
    return (
        '<div class="panel"><div class="metric-label" '
        'style="margin-bottom:8px;">💬 解讀</div>'
        f'<div style="line-height:1.65;color:#e6edf3;">{text}</div></div>'
    )


def _fmt_num(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:.1f}"
    return "—"


def _fmt_price(v) -> str:
    if isinstance(v, (int, float)):
        return f"${v:,.2f}"
    return "—"
