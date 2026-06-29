"""Telegram sender + cache 測試。"""

import json
from datetime import date, datetime
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from daily_xauusd_brief.cache import load_latest, save_latest
from daily_xauusd_brief.models import DailyReport, NewsItem, OhlcBar, PriceSnapshot, TechnicalIndicators
from daily_xauusd_brief.telegram_sender import (
    TelegramSendError,
    _split_text,
    send_brief,
)


# ---------- _split_text ----------

def test_split_text_short_no_split():
    out = _split_text("hello world")
    assert out == ["hello world"]


def test_split_text_long_splits_cleanly():
    text = "line\n\n" * 2000      # > 4096 chars
    parts = _split_text(text)
    assert len(parts) >= 2
    for p in parts:
        assert len(p) <= 4096


# ---------- Bot API path: success ----------

@pytest.mark.asyncio
async def test_send_brief_bot_api_success():
    fake_ok = {"ok": True, "result": {"message_id": 12345, "chat": {"id": 999}}}
    resp = httpx.Response(200, json=fake_ok)

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        return resp

    short_text = "**Bold** short"
    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        result = await send_brief(
            short_text,
            bot_token="dummytoken",
            chat_id="999",
        )
    assert result["ok"] is True
    assert result["result"]["message_id"] == 12345


# ---------- Bot API path: HTTP error ----------

@pytest.mark.asyncio
async def test_send_brief_bot_api_http_error_raises():
    resp = httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        return resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        with pytest.raises(TelegramSendError) as exc_info:
            await send_brief("hello", bot_token="bad", chat_id="999")
    assert exc_info.value.status_code == 401
    assert "401" in str(exc_info.value)


# ---------- Bot API path: API returns not-ok ----------

@pytest.mark.asyncio
async def test_send_brief_bot_api_not_ok_raises():
    body = {"ok": False, "description": "Bad Request: chat not found"}
    resp = httpx.Response(400, json=body)

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        return resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        with pytest.raises(TelegramSendError) as exc_info:
            await send_brief("hello", bot_token="t", chat_id="badchat")
    assert "Bad Request" in str(exc_info.value) or "chat not found" in str(exc_info.value)


# ---------- Bot API path: missing creds ----------

@pytest.mark.asyncio
async def test_send_brief_without_creds_raises(monkeypatch):
    # 確認 env var 也沒有
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(TelegramSendError) as exc_info:
        await send_brief("hello")
    assert "TELEGRAM_BOT_TOKEN" in str(exc_info.value)


# ---------- Hermes path ----------

@pytest.mark.asyncio
async def test_send_brief_hermes_path_raises_with_clear_message():
    with pytest.raises(TelegramSendError) as exc_info:
        await send_brief("hello", bot_token="t", chat_id="c", prefer="hermes")
    assert "Hermes" in str(exc_info.value) or "parent" in str(exc_info.value)


# ---------- Multi-chunk path ----------

@pytest.mark.asyncio
async def test_send_brief_splits_long_text_into_multi_chunks():
    chunks_seen: list[str] = []

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        chunks_seen.append(kwargs["json"]["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    long_text = ("這是一段很長的測試文字 " * 1000) + "\n\n" + ("more " * 500)
    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        await send_brief(long_text, bot_token="t", chat_id="c")
    assert len(chunks_seen) >= 2
    for c in chunks_seen:
        assert len(c) <= 4096


# ---------- Cache save/load ----------

def _sample_report() -> DailyReport:
    return DailyReport(
        symbol="XAU/USD",
        report_date=date(2025, 6, 22),
        generated_at=datetime(2025, 6, 22, 8, 0),
        price=PriceSnapshot(
            symbol="XAU/USD", price=2350.42, change_abs=12.5, change_pct=0.53,
            as_of=datetime(2025, 6, 22, 8, 0),
        ),
        indicators=TechnicalIndicators(ma20=2340.0, trend="bullish"),
        news=[],
        summary="測試",
        ma20_note="現價 $2,350.42 高於 20 日均線 $2,340.00",
    )


def test_cache_save_and_load_round_trip(tmp_path):
    report = _sample_report()
    path = save_latest(report, base_dir=tmp_path)
    assert path.exists()

    loaded = load_latest(base_dir=tmp_path)
    assert loaded is not None
    assert loaded.symbol == report.symbol
    assert loaded.price.price == report.price.price
    assert loaded.price.change_abs == report.price.change_abs


def test_cache_load_returns_none_when_missing(tmp_path):
    result = load_latest(base_dir=tmp_path / "nonexistent")
    assert result is None
