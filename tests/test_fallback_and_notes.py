"""降級報告 + notes load 測試。"""

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from daily_xauusd_brief.compute_indicators import (
    compute_key_levels,
    interpret,
    format_key_levels,
)
from daily_xauusd_brief.format_report import format_markdown, format_telegram
from daily_xauusd_brief.models import (
    DailyReport,
    KeyLevels,
    NewsItem,
    OhlcBar,
    PriceSnapshot,
    PriceStatus,
    TechnicalIndicators,
)
from daily_xauusd_brief.notes import load_notes


# ----- key levels -----

def _bars(n, base=2000.0):
    bars = []
    base_date = date(2025, 1, 1)
    for i in range(n):
        p = base + (i % 10) * 5  # oscillate a bit
        bars.append(
            OhlcBar(
                date=base_date + timedelta(days=i),
                open=p, high=p + 8, low=p - 8, close=p + (1 if i % 2 == 0 else -1),
            )
        )
    return bars


def test_compute_key_levels_short_bars():
    bars = _bars(3)
    kl = compute_key_levels(bars)
    assert kl.prev_high is not None
    assert kl.prev_low is not None
    assert kl.high_5d is None
    assert kl.high_20d is None


def test_compute_key_levels_full_20_bars():
    bars = _bars(25)
    kl = compute_key_levels(bars)
    assert kl.prev_high is not None
    assert kl.high_5d is not None
    assert kl.low_5d is not None
    assert kl.high_20d is not None
    assert kl.low_20d is not None
    # Ensure consistency
    assert kl.high_20d >= kl.low_20d


# ----- interpret heuristic -----

def test_interpret_no_price_returns_failure_message():
    out = interpret(None, None, KeyLevels(), trend="neutral")
    assert "失敗" in out


def test_interpret_price_above_ma20_and_top_of_range():
    kl = KeyLevels(prev_high=2400, prev_low=2350, high_5d=2410, low_5d=2355,
                   high_20d=2420, low_20d=2340)
    out = interpret(price=2420, ma20=2380, levels=kl, trend="bullish")
    assert "20 日均線之上" in out
    assert "上沿" in out  # pos >= 0.8 → 上沿
    assert "失敗" not in out


def test_interpret_price_below_ma20_and_below_range():
    kl = KeyLevels(high_5d=2410, low_5d=2355, high_20d=2420, low_20d=2340)
    out = interpret(price=2330, ma20=2360, levels=kl, trend="bearish")
    assert "20 日均線之下" in out
    assert "下沿" in out


def test_interpret_small_range_no_volatility_hint():
    # 5 日 range 太細 vs 20 日 → 不提"波動擴大"
    kl = KeyLevels(high_5d=2400, low_5d=2395, high_20d=2410, low_20d=2350)
    out = interpret(price=2380, ma20=2375, levels=kl)
    assert "波動擴大" not in out


# ----- formatter: 降級 + 新 sections -----

def _failing_price_report() -> DailyReport:
    """價格 None 的報告。"""
    return DailyReport(
        symbol="XAU/USD",
        report_date=date(2025, 6, 22),
        generated_at=datetime(2025, 6, 22, 8, 0),
        price=PriceSnapshot(
            symbol="XAU/USD",
            price=None,
            change_abs=None,
            change_pct=None,
            as_of=datetime(2025, 6, 22, 8, 0),
            status=PriceStatus(
                primary_ok=False, primary_source="Twelve Data", fallback_used=True,
                message="主來源錯誤：HTTP 401 unauthorized",
            ),
        ),
        indicators=TechnicalIndicators(trend="neutral"),
        news=[],
        summary="今日價格資料取得失敗。",
        ma20_note="現價資料未能取得。",
        news_highlights="本日未抓取到黃金相關新聞。",
    )


def test_format_markdown_price_failure_shows_warning():
    md = format_markdown(_failing_price_report())
    assert "今日價格資料取得失敗" in md
    assert "原因" in md or "主來源" in md
    assert "技術" in md  # 技術分析區塊仍出
    assert "新聞" in md or "新聞焦點" in md


def test_format_telegram_price_failure_uses_warning_emoji():
    text = format_telegram(_failing_price_report())
    assert "⚠️" in text


def test_format_markdown_with_notes_includes_user_notes():
    r = _failing_price_report()
    r.notes = "個人觀察：金價似見頂，留意 2400 阻力位。"
    md = format_markdown(r)
    assert "備註" in md
    assert r.notes in md


def test_format_markdown_with_news_failure_shows_warning():
    r = _failing_price_report()
    # 假 price ok 但 news 0
    r.price = PriceSnapshot(
        symbol="XAU/USD",
        price=2350.0, change_abs=10.0, change_pct=0.5,
        as_of=datetime(2025, 6, 22, 8, 0),
        status=PriceStatus(primary_ok=True, primary_source="Twelve Data"),
    )
    md = format_markdown(r)
    assert "新聞來源全數失敗" in md
    # 但價格仍出
    assert "2,350" in md.replace(",", "") or "2,350.00" in md


def test_format_markdown_notes_empty_shows_placeholder():
    md = format_markdown(_failing_price_report())
    assert "備註" in md
    # placeholder text
    assert "空" in md or "notes/" in md


# ----- format_key_levels 渲染 -----

def test_format_key_levels_with_values():
    kl = KeyLevels(prev_high=2400, prev_low=2350, high_5d=2410, low_5d=2355,
                   high_20d=2420, low_20d=2340)
    out = format_key_levels(kl)
    assert "2,400" in out
    assert "5 日高" in out
    assert "20 日低" in out


def test_format_key_levels_all_none():
    kl = KeyLevels()
    out = format_key_levels(kl)
    for line in out.splitlines():
        assert "—" in line


# ----- notes load -----

def test_load_notes_returns_empty_when_dir_missing(tmp_path):
    assert load_notes(tmp_path, "2025-06-22") == ""


def test_load_notes_specific_date(tmp_path):
    notes_dir = tmp_path / "reports" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "2025-06-22.md").write_text("個人觀察：突破失敗。", encoding="utf-8")
    assert load_notes(tmp_path, "2025-06-22") == "個人觀察：突破失敗。"


def test_load_notes_falls_back_to_latest(tmp_path):
    notes_dir = tmp_path / "reports" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "latest.md").write_text("fallback content", encoding="utf-8")
    assert load_notes(tmp_path, "2025-06-22") == "fallback content"


def test_load_notes_specific_wins_over_latest(tmp_path):
    notes_dir = tmp_path / "reports" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "2025-06-22.md").write_text("specific date", encoding="utf-8")
    (notes_dir / "latest.md").write_text("latest only", encoding="utf-8")
    assert load_notes(tmp_path, "2025-06-22") == "specific date"
