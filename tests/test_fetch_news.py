"""fetch_news 單元測試：newsapi.ai + RSS。"""

import json
from typing import Any
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from daily_xauusd_brief.fetch_news import (
    _normalize_newsapi_ai_article,
    fetch_news_main,
    fetch_news_legacy,
    fetch_rss,
    FetchError,
)


# ---- _normalize_newsapi_ai_article ----

def test_normalize_newsapi_ai_basic():
    art = {
        "title": "Gold rallies on inflation data",
        "url": "https://example.com/x",
        "body": "Long body " * 50,
        "source": {"title": "Reuters"},
        "date": "2026-06-22",
    }
    out = _normalize_newsapi_ai_article(art)
    assert out["title"] == "Gold rallies on inflation data"
    assert out["url"] == "https://example.com/x"
    assert out["source"] == "Reuters"
    assert len(out["description"]) <= 283   # 280 + '…'


def test_normalize_newsapi_ai_missing_title():
    art = {"title": "", "url": "https://example.com/x", "body": "x"}
    assert _normalize_newsapi_ai_article(art) is None


def test_normalize_newsapi_ai_missing_url():
    art = {"title": "t", "url": "", "body": "x"}
    assert _normalize_newsapi_ai_article(art) is None


# ---- fetch_news_main ----

@pytest.mark.asyncio
async def test_fetch_news_main_success_returns_normalized():
    fake_response = {
        "articles": {
            "results": [
                {"title": "Gold up", "url": "https://x.com/1", "body": "b", "source": {"title": "Reuters"}, "sim": 0},
                {"title": "", "url": "https://x.com/2", "body": "b"},   # filtered
            ]
        }
    }
    mock_resp = httpx.Response(200, json=fake_response)
    call_count = {"n": 0}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        call_count["n"] += 1
        return mock_resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        items = await fetch_news_main("dummy-uuid-key", count=5)
    # 多個 keyword 各召一次 endpoint。
    assert call_count["n"] >= 1
    # 每個 keyword 均返 1 aggregated article (Reuters 行) → 多條
    assert all("title" in it and it["title"] == "Gold up" for it in items)
    assert len(items) >= 1


@pytest.mark.asyncio
async def test_fetch_news_main_401_returns_empty():
    mock_resp = httpx.Response(401, json={"error": "not recognized"})

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        return mock_resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        items = await fetch_news_main("bad-key", count=5)
    assert items == []


# ---- fetch_news_legacy (newsapi.org fallback) ----

@pytest.mark.asyncio
async def test_fetch_news_legacy_returns_normalized():
    fake = {
        "status": "ok",
        "articles": [
            {"title": "Gold news", "url": "https://x.com/1", "description": "d",
             "source": {"name": "Reuters"}, "publishedAt": "2026-06-22"},
        ],
    }
    mock_resp = httpx.Response(200, json=fake)

    async def fake_get(self, url, **kwargs):  # noqa: ANN001
        return mock_resp

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        items = await fetch_news_legacy("dummyhex", count=5)
    assert len(items) == 1
    assert items[0]["source"] == "Reuters"


@pytest.mark.asyncio
async def test_fetch_news_legacy_401_returns_empty():
    mock_resp = httpx.Response(401, json={"status": "error", "message": "bad key"})

    async def fake_get(self, url, **kwargs):  # noqa: ANN001
        return mock_resp

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        items = await fetch_news_legacy("bad", count=5)
    assert items == []


# ---- fetch_rss (smoke) ----

@pytest.mark.asyncio
async def test_fetch_rss_returns_empty_when_all_urls_fail(caplog):
    import logging
    logging.getLogger("daily_xauusd_brief.fetch_news")
    # 用一個無效 URL 確保 fallback OK
    items = await fetch_rss(feed_urls=["https://this-domain-does-not-exist.invalid/rss"])
    # 無效網址會被 except 吞，items 應為空
    assert items == []
