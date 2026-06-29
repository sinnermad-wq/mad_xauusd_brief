"""新聞去重 + 排名。"""

from __future__ import annotations

import re
import urllib.parse
from difflib import SequenceMatcher
from typing import Any

from .models import NewsItem

# Tag 命中關鍵詞（按重性排序：第一個命中優先）
TAG_KEYWORDS: dict[str, list[str]] = {
    "central_bank": [
        "fed", "fomc", "powell", "federal reserve",
        "ecb", "lagarde", "boe",
        "central bank", "interest rate", "rate cut", "rate hike",
        "央行", "聯準會", "利率", "降息", "升息",
    ],
    "inflation": [
        "cpi", "pce", "inflation", "consumer price",
        "core inflation", "core pce",
        "通膨", "通脹",
    ],
    "geopolitics": [
        "russia", "ukraine", "israel", "iran", "taiwan",
        "war", "conflict", "tension", "sanction",
        "地緣", "戰爭", "衝突",
    ],
    "usd": [
        "dollar", "dxy", "usd", "treasury", "yield",
        "bond", "10-year",
        "美元", "美債", "殖利率",
    ],
}

# 排名關鍵詞：命中 keyword 為「重點相關」
RELEVANCE_KEYWORDS: list[str] = [
    "fed", "fomc", "powell", "federal reserve",
    "ecb", "central bank", "interest rate",
    "cpi", "pce", "inflation",
    "gold", "bullion", "xauusd",
    "dollar", "dxy", "treasury", "yield",
    "rate cut", "rate hike", "降息", "升息",
    "央行", "聯準會", "利率", "通膨", "美元", "金價", "黃金",
]


def normalize_title(title: str) -> str:
    """標題 normalize：lowercase 去標點、collapse 空白。"""
    s = title.lower()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", s)  # 保留中英數字
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_url(url: str) -> str:
    """URL normalize：去 query + fragment，視為唯一性強者。"""
    if not url:
        return ""
    u = url.split("#", 1)[0]
    parsed = urllib.parse.urlparse(u)
    return f"{parsed.netloc}{parsed.path}".lower().rstrip("/")


def _similar(a: str, b: str) -> float:
    """標題相似度（SequenceMatcher ratio）。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def dedupe_news(items: list[NewsItem]) -> list[NewsItem]:
    """去重：URL 完全匹配或標題相似度 >= 0.6，保留首條（較早抓的）。"""
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    out: list[NewsItem] = []
    for item in items:
        nurl = normalize_url(item.url)
        ntitle = normalize_title(item.title)

        if nurl and nurl in seen_urls:
            continue
        if any(_similar(ntitle, t) >= 0.6 for t in seen_titles):
            continue

        if nurl:
            seen_urls.add(nurl)
        if ntitle:
            seen_titles.append(ntitle)
        out.append(item)
    return out


def classify_tag(title: str, description: str = "") -> str:
    """從標題 / 描述判 tag，依優先順序命中第一個。"""
    blob = f"{title} {description}".lower()
    for tag, kws in TAG_KEYWORDS.items():
        if any(k in blob for k in kws):
            return tag
    return "other"


def relevance_score(title: str, description: str = "") -> float:
    """相關性分數：命中 RELEVANCE_KEYWORDS 加分。"""
    blob = f"{title} {description}".lower()
    score = 0.0
    for kw in RELEVANCE_KEYWORDS:
        if kw in blob:
            score += 1.0
    return score


def rank_news(items: list[NewsItem], top_k: int = 5) -> list[NewsItem]:
    """按 related_score 排序，取 top_k。每人計算一次 tag + score。"""
    decorated = []
    for it in items:
        # 發現已是 NewsItem，假設已有 score（summarizer 負責填）
        decorated.append(it)
    decorated.sort(key=lambda x: x.relevance_score, reverse=True)
    return decorated[:top_k]
