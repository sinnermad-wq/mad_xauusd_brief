"""MVP v1 MVP: MOSL。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from daily_xauusd_brief.compute_indicators import (
    bars_to_dataframe,
    compute_indicators,
    describe_price_vs_ma20,
)
from daily_xauusd_brief.format_report import (
    format_markdown,
    format_telegram,
    filename_for,
)
from daily_xauusd_brief.models import (
    DailyReport,
    OhlcBar,
    PriceSnapshot,
    TechnicalIndicators,
)


# ---------- compute_indicators ----------

def _bars(n: int, base=2000.0, drift=1.0) -> list[OhlcBar]:
    """Make n daily bars linearly up-drifting for reproducible tests."""
    bars = []
    base_date = date(2025, 1, 1)
    for i in range(n):
        p = base + drift * i
        bars.append(
            OhlcBar(
                date=base_date.replace() if i == 0 else _add_days(base_date, i),
                open=p,
                high=p + 5,
                low=p - 5,
                close=p + (0.5 if i % 2 == 0 else -0.5),
            )
        )
    return bars


def _add_days(d: date, n: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=n)


def test_bars_to_dataframe_empty():
    df = bars_to_dataframe([])
    assert df.empty


def test_bars_to_dataframe_sorted_ascending():
    bars = _bars(10)
    df = bars_to_dataframe(bars)
    assert list(df["date"]) == sorted(df["date"])


def test_compute_indicators_too_few_bars_no_ma20():
    bars = _bars(10)
    ind = compute_indicators(bars)
    assert ind.ma20 is None


def test_compute_indicators_sufficient_bars_has_ma20():
    bars = _bars(40)
    ind = compute_indicators(bars)
    assert ind.ma20 is not None
    assert ind.ma20 > 0


def test_describe_price_above_ma20():
    text = describe_price_vs_ma20(price=2400.0, ma20=2350.0)
    assert "高於" in text
    assert "20 日均線" in text


def test_describe_price_below_ma20():
    text = describe_price_vs_ma20(price=2300.0, ma20=2350.0)
    assert "低於" in text


def test_describe_price_no_ma20():
    text = describe_price_vs_ma20(price=2400.0, ma20=None)
    assert "資料不足" in text


# ---------- format_report ----------

def _sample_report() -> DailyReport:
    return DailyReport(
        symbol="XAU/USD",
        report_date=date(2025, 6, 22),
        generated_at=datetime(2025, 6, 22, 8, 0),
        price=PriceSnapshot(
            symbol="XAU/USD",
            price=2350.42,
            change_abs=12.5,
            change_pct=0.53,
            as_of=datetime(2025, 6, 22, 8, 0),
        ),
        indicators=TechnicalIndicators(
            ma20=2340.0,
            trend="bullish",
        ),
        news=[],
        summary="現價 $2,350.42，高於 20 日均線 $2,340.00，技術面向偏多。",
        ma20_note="現價高於 20 日均線 $2,340.00",
    )


def test_format_markdown_contains_sections():
    md = format_markdown(_sample_report())
    assert "XAUUSD 每日簡報" in md
    assert "## 💰 價格" in md
    assert "## 📊 技術" in md
    assert "20 日均線" in md
    assert "2,350.42" in md


def test_format_telegram_contains_key_facts():
    text = format_telegram(_sample_report())
    assert "XAUUSD" in text
    assert "2,350.42" in text
    assert "20 日均線" in text or "MA20" in text
    assert "🟢" in text or "🔴" in text or "⚪" in text
    # 手機友善：簡潔、不長到電話睇唔晒
    assert len(text) < 800


def test_filename_format():
    fname = filename_for(datetime(2025, 6, 22, 8, 0))
    # Windows 路徑用 ¦，跨平台：用 Path parts 查
    parts = list(fname.parts)
    assert parts[-1] == "2025-06-22.md"
    assert parts[0] == "reports"
    assert parts[1] == "gold"
