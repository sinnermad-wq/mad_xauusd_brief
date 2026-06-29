"""Telegram 訊息發送模組（獨立）。

兩個路徑：
1. **Telegram Bot API** (HTTP) — 使用 `TELEGRAM_BOT_TOKEN` 直接 POST sendMessage。
   適合 timer / cron 在 Hermes 外自動跑。全套本地控制。
2. **Hermes send_message** — 完全依 Hermes parent。適合 Hermes parent 模式驅動。

以 env vars 決定選道：
- 設了 `TELEGRAM_BOT_TOKEN` 同 `TELEGRAM_CHAT_ID` → Bot API (獨立)
- 否則 → fallback Hermes `send_message` (parent)。

任何送出失敗都 raise + log 明确訊息，船 checkening。"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram 訊息長度 cap。折成兩訊息發送。
TELEGRAM_MAX_LENGTH = 4096


class TelegramSendError(Exception):
    """Telegram 發送失敗。"""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ----- Helpers -----

def _split_text(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """尊重 Telegram 訊息長度 cap；切多段。"""
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    rest = text
    while len(rest) > max_len:
        # 嘗試在最近空行或換行處切
        cut = rest.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = rest.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            cut = max_len
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    parts.append(rest)
    return parts


# ----- Path 1: Telegram Bot API (HTTP) -----

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    reraise=True,
)
async def _send_via_bot_api(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "Markdown",
) -> dict[str, Any]:
    """直接呼叫 Telegram Bot API `sendMessage`。

    Returns: API JSON response `{"ok": True, "result": {...}}`.
    Raises: TelegramSendError on any HTTP / API error.
    """
    chunks = _split_text(text)
    last_result: dict[str, Any] = {}
    for idx, chunk in enumerate(chunks, 1):
        url = TELEGRAM_API_BASE.format(token=bot_token)
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.error(
                "Telegram Bot API HTTP %s (chunk %d/%d): %s",
                resp.status_code, idx, len(chunks), resp.text[:300],
            )
            # 包 body 詳細説明讓 caller 可看
            detail = ""
            try:
                detail = resp.json().get("description", "") or ""
            except Exception:  # noqa: BLE001
                detail = resp.text[:200]
            raise TelegramSendError(
                f"Telegram Bot API HTTP {resp.status_code}: {detail}".strip(": "),
                status_code=resp.status_code,
                body=resp.text[:400],
            )
        body = resp.json()
        if not body.get("ok"):
            logger.error("Telegram Bot API returned not ok: %s", body)
            raise TelegramSendError(
                f"Telegram API error: {body.get('description', 'unknown')}",
                status_code=resp.status_code,
                body=resp.text[:400],
            )
        last_result = body
        logger.info(
            "Telegram Bot API chunk %d/%d sent, message_id=%s",
            idx, len(chunks), body.get("result", {}).get("message_id"),
        )
    return last_result


# ----- Path 2: Hermes send_message (parent) -----

async def _send_via_hermes(text: str) -> None:
    """Hermes parent send_message — 起 logger + dummy 介記。

    Hermes parent 不是 sync calling context：
    - 在 Hermes parent 對話中：由 Hermes 自己取 chat_id、向用戶發送。
    - 在純 standalone（cron 振 prime）: Hermes parent 不在，本函式
      東為 “no-op” + log，JOB到的任務必須 fill `TELEGRAM_BOT_TOKEN` 走 Bot API。
    """
    logger.warning(
        "send_via_hermes() invoked: Hermes parent context not available in this process. "
        "Treat as best-effort log only. Use Bot API instead for reliable delivery."
    )
    logger.info("[hermes-stub] would send to parent chat (%d chars): %s",
                len(text), text[:200].replace("\n", " ") + ("..." if len(text) > 200 else ""))
    raise TelegramSendError(
        "Hermes send_message is only available in parent agent context; "
        "set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to use Bot API directly.",
    )


# ----- Public entry -----

async def send_brief(
    text: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    prefer: str = "bot_api",
) -> dict[str, Any]:
    """Main entry: 發送 Telegram 訊息。

    Args:
        text: 將要發送的訊息。
        bot_token: Telegram Bot API token。如 None，會讀 $TELEGRAM_BOT_TOKEN。
        chat_id: Telegram chat id。如 None，會讀 $TELEGRAM_CHAT_ID。
        prefer: "bot_api" (default) or "hermes"。

    Returns:
        Bot API path 回傳 Telegram API JSON；若走 Hermes path 會 raise。

    Raises:
        TelegramSendError: 發送所有ハチ。
    """
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    logger.info(
        "send_brief called (len=%d, prefer=%s, bot_token_present=%s, chat_id_present=%s)",
        len(text), prefer, bool(bot_token), bool(chat_id),
    )

    if prefer == "hermes":
        return await _send_via_hermes(text)

    # Bot API path —必填 token + chat
    if not bot_token or not chat_id:
        msg = "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to send via Bot API"
        logger.error(msg)
        raise TelegramSendError(msg)

    return await _send_via_bot_api(bot_token, chat_id, text)


def should_send() -> bool:
    """判斷在現 environment 下能否實際送出 Telegram 訊息。"""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(os.environ.get("TELEGRAM_CHAT_ID"))
