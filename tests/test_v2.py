"""MVP v2 測試：news dedupe + rank + summarize + format。"""

from datetime import date, datetime

import pytest

from daily_xauusd_brief.format_report import format_markdown, format_telegram
from daily_xauusd_brief.models import DailyReport, NewsItem, PriceSnapshot, TechnicalIndicators
from daily_xauusd_brief.news_ranker import (
    classify_tag,
    dedupe_news,
    normalize_title,
    normalize_url,
    rank_news,
    relevance_score,
)
from daily_xauusd_brief.news_summarizer import annotate, build_summary_zh, news_highlights


# ---------- normalize ----------

def test_normalize_title_strips_punctuation():
    assert normalize_title("Fed Suggests: Rate Cut?") == "fed suggests rate cut"


def test_normalize_title_handles_chinese():
    assert "聯準會" in normalize_title("聯準會放鴿！利率走向何方？")


def test_normalize_url_strips_query():
    u = "https://Example.com/Article/123?utm_source=feed&fb=1"
    assert normalize_url(u) == "example.com/article/123"


def test_normalize_url_empty():
    assert normalize_url("") == ""


# ---------- dedupe ----------

def test_dedupe_by_url():
    a = NewsItem(title="Fed signals rate cut", source="A", url="https://x.com/1")
    b = NewsItem(title="Fed signals rate cut today", source="B", url="https://x.com/1")
    out = dedupe_news([a, b])
    assert len(out) == 1
    assert out[0].source == "A"


def test_dedupe_by_title_similarity():
    a = NewsItem(title="Powell signals patience on rate cuts", source="A", url="https://a.com/1")
    b = NewsItem(title="Powell Signals Patience on Rate Cuts Wednesday", source="B", url="https://b.com/2")
    out = dedupe_news([a, b])
    assert len(out) == 1


def test_dedupe_keeps_distinct():
    a = NewsItem(title="Fed signals patience", source="A", url="https://a.com/1")
    b = NewsItem(title="Russia Ukraine conflict escalates", source="B", url="https://b.com/2")
    out = dedupe_news([a, b])
    assert len(out) == 2


# ---------- tag / score ----------

def test_classify_tag_central_bank():
    assert classify_tag("Federal Reserve signals rate cut") == "central_bank"


def test_classify_tag_inflation():
    assert classify_tag("US CPI rises to 3.5%") == "inflation"


def test_classify_tag_geopolitics():
    assert classify_tag("Israel Iran tensions rise") == "geopolitics"


def test_classify_tag_usd():
    assert classify_tag("Dollar index climbs on treasury yields") == "usd"


def test_classify_tag_other():
    assert classify_tag("Apple announces new iPhone") == "other"


def test_relevance_score_counting():
    s = relevance_score("Fed rate cut impacts gold and dollar")
    assert s >= 4  # fed, rate cut, gold, dollar


def test_relevance_score_zero():
    s = relevance_score("Sports news")
    assert s == 0.0


# ---------- rank ----------

def test_rank_news_sort_by_score():
    low = NewsItem(title="Apple launches product", source="A", url="u1", relevance_score=0.0)
    high = NewsItem(title="Fed signals rate cut gold", source="B", url="u2", relevance_score=5.0)
    mid = NewsItem(title="Inflation data released today", source="C", url="u3", relevance_score=3.0)
    out = rank_news([low, high, mid], top_k=2)
    assert [x.source for x in out] == ["B", "C"]


# ---------- summarize ----------

def test_annotate_fills_tag_score_summary():
    n = NewsItem(
        title="Fed signals rate cut",
        source="Reuters",
        url="https://x.com/1",
        description="Federal Reserve signaled patience.",
    )
    annotate([n])
    assert n.tag == "central_bank"
    assert n.relevance_score > 0
    assert "聯準會" in n.summary_zh or "央行" in n.summary_zh


def test_build_summary_zh_uses_description():
    out = build_summary_zh("Title", "Some news body here", "central_bank")
    assert "Some news body here" in out


def test_build_summary_zh_no_description():
    out = build_summary_zh("Only Title", "", "central_bank")
    assert "Only Title" in out


def test_news_highlights_empty():
    assert "未抓取到" in news_highlights([])


def test_news_highlights_with_items():
    items = [
        NewsItem(title="Fed rate cut signals", source="A", url="u1", tag="central_bank"),
        NewsItem(title="Inflation rises", source="B", url="u2", tag="inflation"),
        NewsItem(title="Russia war", source="C", url="u3", tag="geopolitics"),
    ]
    h = news_highlights(items)
    assert "央行政策" in h
    assert "通膨" in h
    assert "地緣政治" in h


# ---------- formatter v2 ----------

def _sample_report_with_news() -> DailyReport:
    return DailyReport(
        symbol="XAU/USD",
        report_date=date(2025, 6, 22),
        generated_at=datetime(2025, 6, 22, 8, 0),
        price=PriceSnapshot(
            symbol="XAU/USD", price=2350.42, change_abs=12.5, change_pct=0.53,
            as_of=datetime(2025, 6, 22, 8, 0),
        ),
        indicators=TechnicalIndicators(ma20=2340.0, trend="bullish"),
        news=[
            NewsItem(
                title="Fed signals patience on rate cuts",
                source="Reuters",
                url="https://example.com/news/1",
                description="The Federal Reserve said it would be patient.",
                tag="central_bank",
                summary_zh="事件：聯準會表示將保持耐心。影響機制：央行密集種是金價最重要的動能。",
                relevance_score=5.0,
            ),
            NewsItem(
                title="US CPI rose 0.3%",
                source="Bloomberg",
                url="https://example.com/news/2",
                description="US inflation data came in slightly hot.",
                tag="inflation",
                summary_zh="事件：美國 CPI 高於預期。影響機制：通膨升溫推高金價。",
                relevance_score=4.0,
            ),
        ],
        summary="收盤 $2,350.42。現價 $2,350.42 高於 20 日均線 $2,340.00，差距 +10.42 (+0.45%)。技術面向偏多。",
        ma20_note="現價 $2,350.42 高於 20 日均線 $2,340.00，差距 +10.42 (+0.45%)。",
        news_highlights="本日黃金/宏觀以央行政策 1條、通膨 1條為主。重點事件：Fed signals patience on rate cuts。須留意以上事件在金價上的傳導路径。",
    )


def test_format_markdown_v2_has_news_section():
    md = format_markdown(_sample_report_with_news())
    assert "## 📰 黃金/宏觀新聞" in md
    assert "Fed signals patience" in md
    assert "央行政策" in md
    assert "## 🎯 今日黃金重點" in md


def test_format_telegram_v3_short_no_optional_sections():
    """v3: short Telegram（≤ 11 行）無 key_levels block, 完整版靠 MD path."""
    tg = format_telegram(_sample_report_with_news())
    # Marker
    assert tg.startswith("🥇")
    # 不再含完整關鍵價位段（移咗去 MD）
    assert "*🔑 關鍵價位*" not in tg
    # 不再每條新聞列 source + link (短版只列短句)
    assert "(Reuters)" not in tg
    assert "https://example.com" not in tg
    # 行數 count
    non_empty = [line for line in tg.splitlines() if line.strip()]
    assert len(non_empty) <= 14, f"expected ≤14 lines, got {len(non_empty)}"
    # header / 技術 / 重點 / footer 齊
    assert "技術" in tg
    assert "MA20" in tg
    assert "重點" in tg.lower() or "本日" in tg  # news_highlights 開首


def test_format_telegram_v3_short_news_lines_truncated():
    """新聞 line 鎖 ≤60 字, 超長 trunc 標 …"""
    long_summary = "x" * 200 + " 影響機制: 通膨升溫推高金價"
    rep = _sample_report_with_news()
    rep.news[0].summary_zh = long_summary
    tg = format_telegram(rep)
    assert "…" in tg
    # Ensure no leaked > 80 char item lines
    for line in tg.splitlines():
        if line.lstrip().startswith("1."):
            assert len(line.strip()) <= 80


def test_format_telegram_v3_md_path_in_footer():
    """如果提供 md_path, 應該出現在 footer."""
    tg = format_telegram(_sample_report_with_news(), md_path="reports/gold/2025-06-22.md")
    assert "reports/gold/2025-06-22.md" in tg
    assert tg.rstrip().endswith("_")  # disclaimer 仲係最後
