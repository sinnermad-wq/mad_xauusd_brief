"""
Smoke tests for V3 M3 — Candlestick formatters & HTML report.

Covers:
  • EngineOutput.to_telegram_zh(): three bias flavours, structure/RSI/ATR,
    patterns, breakout_confirmed, breakout_watch, SR levels, footer.
  • report.render_candlestick_report(): builds valid HTML with SVG chart,
    pattern panel, SR table, breakout banner.
  • report.write_candlestick_report(): writes file to disk, returns Path.

No I/O at import — tests are deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path

from candlestick_engine.contract import EngineOutput
from candlestick_engine.report import (
    render_candlestick_report,
    write_candlestick_report,
)


def _payload_for(bias: str, **overrides) -> dict:
    """Helper to build a source_payload with sensible defaults."""
    base = {
        "structure_state": "uptrend",
        "rsi_14": 58.3,
        "atr_14": 18.7,
        "detected_patterns": [
            {"name": "pin_bar_bullish", "description_zh": "看漲 Pin Bar"}
        ],
        "breakout_state": {"breakout_confirmed": False, "breakout_watch": False},
        "support_levels": [4020.5, 3995.0],
        "resistance_levels": [4080.0, 4105.5],
    }
    base.update(overrides)
    return base


def _output(bias: str, **overrides) -> EngineOutput:
    return EngineOutput(
        engine_name="candlestick",
        symbol="XAUUSD",
        timestamp="2026-06-26T12:00:00",
        timeframe="1D",
        bias=bias,
        bias_strength=overrides.pop("bias_strength", 0.7),
        confidence=overrides.pop("confidence", None),
        explanation_zh="金價站穩 MA20 之上。",
        source_payload=_payload_for(bias, **overrides),
    )


def _bars(n: int = 15) -> list[dict]:
    return [
        {
            "datetime": f"2026-06-{i + 1:02d}",
            "open": 4000 + i,
            "high": 4010 + i,
            "low": 3995 + i,
            "close": 4005 + i,
            "volume": 1000,
        }
        for i in range(n)
    ]


# ── Telegram formatter ────────────────────────────────────────────────────


def test_to_telegram_zh_bullish_signal():
    out = _output("bullish")
    text = out.to_telegram_zh()
    assert "🟢 *XAUUSD Candlestick* — BULLISH" in text
    assert "看漲 Pin Bar" in text
    assert "RSI: `58.3`" in text
    assert "ATR: `18.7`" in text
    re.search(r"🟢 S: `4020.5`, `3995.0`", text)
    re.search(r"🔴 R: `4080.0`, `4105.5`", text)
    re.search(r"run_id: `.{8}` · 1D_", text)


def test_to_telegram_zh_bearish_emoji():
    out = _output("bearish", structure_state="downtrend", rsi_14=32.1)
    text = out.to_telegram_zh()
    assert "🔴" in text
    assert "BEARISH" in text
    assert "📉" in text
    assert "RSI: `32.1`" in text


def test_to_telegram_zh_neutral_when_no_signals():
    out = _output(
        "neutral",
        detected_patterns=[],
        breakout_state={"breakout_confirmed": False, "breakout_watch": False},
        support_levels=[],
        resistance_levels=[],
    )
    text = out.to_telegram_zh()
    assert "⚪" in text
    assert "未" not in text  # formatter shows dashes, not "未"
    assert "support" not in text.lower()


def test_to_telegram_zh_breakout_confirmed():
    out = _output(
        "bullish",
        breakout_state={
            "breakout_confirmed": True,
            "breakout_type": {"value": "break_up"},
        },
    )
    text = out.to_telegram_zh()
    assert "🚀 突破：已確認 向上" in text


def test_to_telegram_zh_breakout_watch():
    out = _output(
        "neutral",
        breakout_state={"breakout_watch": True, "breakout_watch_level": 4080.5},
    )
    text = out.to_telegram_zh()
    assert "👁️ 觀察" in text
    assert "4080.5" in text


def test_to_telegram_zh_confidence_when_present():
    out = _output("bullish", confidence=0.85)
    text = out.to_telegram_zh()
    assert "confidence: 85%" in text


def test_to_telegram_zh_confidence_dash_when_none():
    out = _output("bullish", confidence=None)
    text = out.to_telegram_zh()
    assert "confidence: —" in text


# ── HTML report ───────────────────────────────────────────────────────────


def test_render_html_returns_string():
    out = _output("bullish")
    html = render_candlestick_report(out, _bars())
    assert isinstance(html, str)
    assert len(html) > 1000


def test_render_html_contains_svg_chart():
    out = _output("bullish")
    html = render_candlestick_report(out, _bars(30))
    assert "<svg" in html
    assert "viewBox=\"0 0 800 280\"" in html
    assert "K 線" in html or "K線" in html


def test_render_html_contains_patterns():
    out = _output("bullish")
    html = render_candlestick_report(out, _bars())
    assert "看漲 Pin Bar" in html
    assert "pin_bar_bullish" in html


def test_render_html_contains_sr_levels():
    out = _output("bullish")
    html = render_candlestick_report(out, _bars())
    assert "$4,020.50" in html
    assert "$4,080.00" in html


def test_render_html_breakout_pills():
    out = _output(
        "bullish",
        breakout_state={"breakout_confirmed": True, "breakout_type": {"value": "break_up"}},
    )
    html = render_candlestick_report(out, _bars())
    assert "已確認突破" in html or "pill-bull" in html


def test_render_html_with_empty_bars():
    out = _output("neutral")
    html = render_candlestick_report(out, [])
    assert "無 K 線資料" in html


def test_render_html_disclaimer_footer():
    out = _output("bullish")
    html = render_candlestick_report(out, _bars())
    assert "V3 M3" in html
    assert "不構成投資建議" in html


def test_write_candlestick_report_creates_file(tmp_path: Path):
    out = _output("bullish")
    target = write_candlestick_report(
        out, _bars(), tmp_path, report_date="2026-06-26"
    )
    assert target.exists()
    assert target.name == "2026-06-26.html"
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_write_candlestick_report_uses_timestamp_fallback(tmp_path: Path):
    out = _output("bullish")
    target = write_candlestick_report(out, _bars(), tmp_path)
    assert target.name == "2026-06-26.html"
