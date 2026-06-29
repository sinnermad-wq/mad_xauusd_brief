"""pydantic 資料模型：定義 report / price / news 各單位的結構。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


# ----- 價格與指標 -----

class PriceStatus(BaseModel):
    """價格資料來源狀態 (降級報告)。"""

    primary_ok: bool = False
    primary_source: str = ""
    fallback_used: bool = False
    message: str = ""


class OhlcBar(BaseModel):
    """單根 K 線（OHLC）。"""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class PriceSnapshot(BaseModel):
    """當前現價快照。"""

    symbol: str
    price: float | None = None      # 未取得時為 None
    change_abs: float | None = None
    change_pct: float | None = None
    as_of: datetime
    status: PriceStatus = Field(default_factory=PriceStatus)


class KeyLevels(BaseModel):
    """關鍵價位。皆可為 None 以保證降級報告。"""

    prev_high: float | None = None
    prev_low: float | None = None
    high_5d: float | None = None
    low_5d: float | None = None
    high_20d: float | None = None
    low_20d: float | None = None



class TechnicalIndicators(BaseModel):
    """技術指標集合。"""

    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    ma20: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    trend: Literal["bullish", "bearish", "neutral"] = "neutral"


# ----- 新聞 -----

class NewsItem(BaseModel):
    """單則新聞。"""

    title: str
    source: str
    url: str
    published_at: datetime | None = None
    description: str | None = None
    # v2 additions
    tag: Literal["central_bank", "inflation", "geopolitics", "usd", "other"] = "other"
    summary_zh: str = ""
    relevance_score: float = 0.0


# ----- 報告 -----

class DailyReport(BaseModel):
    """每日報告整體結構。"""

    symbol: str
    report_date: date
    generated_at: datetime
    price: PriceSnapshot
    indicators: TechnicalIndicators
    news: list[NewsItem] = Field(default_factory=list)
    summary: str = ""
    ma20_note: str = ""
    news_highlights: str = ""     # v2: 今日黃金重點小結
    key_levels: KeyLevels = Field(default_factory=KeyLevels)
    interpretation: str = ""
    notes: str = ""               # 使用者手動補丁
    disclaimer: str = "以上為研究摘要，不構成投資建議。"
