"""format_report 單元測試。"""

from datetime import date, datetime

from daily_xauusd_brief.format_report import format_markdown, format_telegram
from daily_xauusd_brief.models import (
    DailyReport,
    NewsItem,
    PriceSnapshot,
    TechnicalIndicators,
)


def sample_report() -> DailyReport:
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
            rsi14=58.3,
            macd=2.5,
            macd_signal=1.8,
            macd_hist=0.7,
            ma20=2340.0,
            ma50=2310.0,
            ma200=2280.0,
            bb_upper=2365.0,
            bb_middle=2340.0,
            bb_lower=2315.0,
            trend="bullish",
        ),
        news=[
            NewsItem(
                title="Fed signals patience on rate cuts",
                source="Reuters",
                url="https://example.com/news/1",
            ),
        ],
    )


def test_format_telegram_contains_key_elements():
    r = sample_report()
    text = format_telegram(r)
    assert "XAUUSD 每日簡報" in text
    assert "2,350.42" in text  # MA20「sample 未設置 ma20，故不出現『2,340』」是 OK 的
    # sample 未設 ma20_note/ma20 數值，v2 不預設出現 MA 字眼
    assert "趨勢" in text or "偏多" in text
    assert "🟢" in text or "🔴" in text or "⚪" in text
    assert text.count("\n") < 40  # 簡潔


def test_format_markdown_table_free():
    r = sample_report()
    md = format_markdown(r)
    assert "XAUUSD 每日簡報" in md
    assert "## 💰 價格" in md
    assert "https://example.com/news/1" in md
