"""compute_indicators 單元測試。"""

from datetime import date, timedelta

import pytest

from daily_xauusd_brief.compute_indicators import (
    bars_to_dataframe,
    compute_indicators,
)
from daily_xauusd_brief.models import OhlcBar


def make_bars(n: int) -> list[OhlcBar]:
    """產生 n 根 mock bars，價格線性遞增。"""
    bars = []
    base = date(2025, 1, 1)
    price = 2000.0
    for i in range(n):
        bars.append(
            OhlcBar(
                date=base + timedelta(days=i),
                open=price,
                high=price + 5,
                low=price - 5,
                close=price + (1 if i % 2 == 0 else -1),
            )
        )
        price += 1
    return bars


def test_bars_to_dataframe_empty():
    df = bars_to_dataframe([])
    assert df.empty


def test_bars_to_dataframe_orders_by_date():
    bars = make_bars(5)
    df = bars_to_dataframe(bars)
    assert list(df["date"]) == sorted(df["date"])


def test_compute_indicators_too_few_bars():
    bars = make_bars(5)
    ind = compute_indicators(bars)
    assert ind.rsi14 is None
    assert ind.ma20 is None


def test_compute_indicators_sufficient_bars():
    bars = make_bars(220)
    ind = compute_indicators(bars)
    assert ind.ma20 is not None
    # v1 範圍：MA50/MA200/RSI/MACD/BB 未實作，留 None
    assert ind.ma50 is None
    assert ind.ma200 is None
    assert ind.rsi14 is None
    assert ind.bb_upper is None
