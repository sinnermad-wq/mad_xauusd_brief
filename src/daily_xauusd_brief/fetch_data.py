"""資料抓取：Twelve Data（行情）。MVP v1 只接行情，新聞留接 接。"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

TWELVE_DATA_BASE = "https://api.twelvedata.com"


class FetchError(Exception):
    """Twelve Data fetch failed."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def fetch_time_series(
    api_key: str,
    symbol: str = "XAU/USD",
    interval: str = "1day",
    outputsize: int = 30,
) -> list[dict[str, Any]]:
    """Twelve Data 日線 OHLC。

    MVP v1 pre差預拉 30 天（足以計算 MA20），
    之後可調高至 200 納 MA50/200。
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": api_key,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{TWELVE_DATA_BASE}/time_series", params=params)
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("status") == "error":
        raise FetchError(payload.get("message", "Twelve Data error"))
    return payload.get("values", [])
