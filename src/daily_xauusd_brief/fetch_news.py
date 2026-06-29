"""新聞抓取：newsapi.ai (Event Registry) 主路由 + newsapi.org fallback。"""

from __future__ import annotations

import logging
from typing import Any

import feedparser
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# newsapi.ai / eventregistry.org — primary source
NEWSAPI_AI_ENDPOINT = "https://eventregistry.org/api/v1/article/getArticles"

# newsapi.org — fallback (different company, 32-char hex key format)
NEWSAPI_ORG_ENDPOINT = "https://newsapi.org/v2/everything"

USER_AGENT = "daily-xauusd-brief/0.3 (+https://github.com/local/daily-xauusd-brief)"

# RSS feeds — fallback / augmentation source
DEFAULT_RSS_FEEDS: list[str] = [
    "https://www.investing.com/rss/news_11.rss",   # metals / commodities
    "https://www.kitco.com/rss/news/",             # Kitco news
]


class FetchError(Exception):
    """新聞 fetch 失敗。"""

    def __init__(self, message: str, status_code: int | None = None, source: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.source = source


# ----- primary: newsapi.ai (Event Registry) -----

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def fetch_newsapi_ai(
    api_key: str,
    keyword: str = "gold",
    lang: str = "eng",
    date_window_days: int = 7,
    count: int = 15,
) -> list[dict[str, Any]]:
    """Event Registry newsapi.ai primary endpoint。

    POST JSON body to https://eventregistry.org/api/v1/article/getArticles

    注意：Event Registry `keywordLoc=body` 在 free tier 偶爾不返 results，
    以 `title` 較可靠；keyword 盡量用單烆。"
    """
    body = {
        "apiKey": api_key,
        "query": {
            "lang": lang,
            "$query": {
                "keyword": keyword,
                "keywordLoc": "title",  # 與 body 較 reliable
            },
            "dateStart": f"now-{date_window_days}d",
            "dateEnd": "now",
        },
        "resultType": "articles",
        "articlesSortBy": "date",
        "articlesCount": count,
        "includeConcepts": False,
        "includeDuplicate": False,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        resp = await client.post(NEWSAPI_AI_ENDPOINT, json=body)
    if resp.status_code == 401:
        raise FetchError("apikey not recognized", 401, source="newsapi.ai")
    if resp.status_code == 429:
        raise FetchError("rate limit reached", 429, source="newsapi.ai")
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code, source="newsapi.ai")

    payload = resp.json()
    if payload.get("error"):
        # newsapi.ai returns 200 with error payload if query malformed
        raise FetchError(payload["error"], 200, source="newsapi.ai")
    return payload.get("articles", {}).get("results", []) or []


def _normalize_newsapi_ai_article(a: dict[str, Any]) -> dict[str, Any] | None:
    """Map Event Registry article → 內部欄位。"""
    url = a.get("url") or ""
    title = a.get("title") or ""
    if not url or not title:
        return None
    source_obj = a.get("source") or {}
    src_name = source_obj.get("title") if isinstance(source_obj, dict) else "newsapi.ai"
    body = a.get("body") or ""
    description = (body[:280] + "…") if len(body) > 280 else body
    date = a.get("dateTimePub") or a.get("dateTime") or ""
    return {
        "title": title,
        "url": url,
        "source": src_name or "newsapi.ai",
        "description": description,
        "date": date,
        "sim": a.get("sim", 0),
    }


async def fetch_news_main(
    api_key: str,
    count: int = 15,
) -> list[dict[str, Any]]:
    """Primary fetch — newsapi.ai 多 keyword 滑動。

    Returns: 統一 dict 格式 list。
    """
    keywords = ["gold", "XAUUSD", "Federal Reserve", "central bank gold"]
    all_items: list[dict[str, Any]] = []
    for kw in keywords:
        try:
            raw = await fetch_newsapi_ai(api_key, keyword=kw, count=count)
            for a in raw:
                norm = _normalize_newsapi_ai_article(a)
                if norm:
                    all_items.append(norm)
        except FetchError as e:
            logger.warning("newsapi.ai (%s) failed [%s]: %s", kw, e.status_code, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("newsapi.ai (%s) unexpected error: %s", kw, e)

    if all_items:
        logger.info("fetched %d articles from newsapi.ai (multi-kw)", len(all_items))
        return all_items
    logger.warning("newsapi.ai returned 0 articles across all keywords")
    return []


# ----- fallback: legacy newsapi.org -----

@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=5))
async def _legacy_fetch_newsapi_org(api_key: str, count: int = 10) -> list[dict[str, Any]]:
    """Legacy newsapi.org fallback (only used if key 為 32-char hex 格式)。"""
    params = {
        "q": "gold OR XAUUSD OR bullion",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": count,
        "apiKey": api_key,
    }
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        resp = await client.get(NEWSAPI_ORG_ENDPOINT, params=params)
    if resp.status_code >= 400:
        raise FetchError(f"newsapi.org HTTP {resp.status_code}", resp.status_code, source="newsapi.org")
    payload = resp.json()
    if payload.get("status") == "error":
        raise FetchError(payload.get("message", "newsapi.org error"), 401, source="newsapi.org")
    articles = payload.get("articles", [])
    out: list[dict[str, Any]] = []
    for a in articles:
        if not a.get("title") or not a.get("url"):
            continue
        src = a.get("source") or {}
        out.append({
            "title": a["title"],
            "url": a["url"],
            "source": src.get("name", "newsapi.org") if isinstance(src, dict) else str(src),
            "description": a.get("description") or "",
            "date": a.get("publishedAt", ""),
            "sim": 0,
        })
    return out


async def fetch_news_legacy(api_key: str, count: int = 10) -> list[dict[str, Any]]:
    """Legacy newsapi.org 試行 (key 格式如為 32-char hex 才有意義)。"""
    try:
        return await _legacy_fetch_newsapi_org(api_key, count=count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsapi.org fallback failed: %s", exc)
        return []


# ----- RSS 補充 -----

async def fetch_rss(feed_urls: list[str] | None = None) -> list[dict[str, Any]]:
    """從 RSS feed 拿文章補充。"""
    feeds = feed_urls or DEFAULT_RSS_FEEDS
    out: list[dict[str, Any]] = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            feed_title = ""
            if hasattr(parsed, "feed") and parsed.feed:
                feed_title = parsed.feed.get("title", "")
            for e in parsed.entries[:10]:
                title = getattr(e, "title", "") or ""
                link = getattr(e, "link", "") or ""
                if not title or not link:
                    continue
                desc = getattr(e, "summary", "") or getattr(e, "description", "") or ""
                date = getattr(e, "published", "") or getattr(e, "updated", "") or ""
                out.append({
                    "title": title,
                    "url": link,
                    "source": feed_title or "RSS",
                    "description": desc[:280],
                    "date": date,
                    "sim": 0,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("RSS %s 抓取失敗: %s", url, exc)
    return out
