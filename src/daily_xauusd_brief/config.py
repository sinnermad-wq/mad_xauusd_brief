"""設定載入：從 .env 讀取 API keys、chat id 與排程參數。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    """所有設定集中於此。"""

    twelve_data_api_key: str
    newsapi_key: str
    deepseek_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    symbol: str = "XAU/USD"
    report_hour: int = 8
    report_minute: int = 0
    timezone: str = "Asia/Hong_Kong"
    enable_llm_summary: bool = True
    llm_provider: str = "nim"
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"

    # V3 M4 — validation layer; see candlestick_engine.validation
    timeframe_stack: tuple[str, ...] = ("1day", "4h", "1h")
    tf_alignment_weight: float = 0.4
    cross_engine_weight: float = 0.35
    data_quality_weight: float = 0.25
    sanity_gap_pct_threshold: float = 0.10  # 10% gap -> soft flag
    sanity_min_bars_per_tf: int = 14        # below this counts as soft flag

    @classmethod
    def from_env(cls) -> "Config":
        """從環境變數載入設定。"""
        twelve = os.getenv("TWELVE_DATA_API_KEY", "").strip()
        news = os.getenv("NEWSAPI_KEY", "").strip()
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

        if not twelve:
            raise ValueError("TWELVE_DATA_API_KEY is required (set in .env)")
        if not news:
            raise ValueError("NEWSAPI_KEY is required (set in .env)")

        # Parse comma-separated timeframe stack, e.g. TF_STACK="4h,1h,15m"
        tf_env = os.getenv("TF_STACK", "1day,4h,1h").strip()
        tf_stack = tuple(s.strip() for s in tf_env.split(",") if s.strip())
        if not tf_stack:
            tf_stack = ("1day", "4h", "1h")

        return cls(
            twelve_data_api_key=twelve,
            newsapi_key=news,
            deepseek_api_key=deepseek_key,
            telegram_bot_token=telegram_token,
            telegram_chat_id=telegram_chat,
            symbol=os.getenv("SYMBOL", "XAU/USD"),
            report_hour=int(os.getenv("REPORT_HOUR", "8")),
            report_minute=int(os.getenv("REPORT_MINUTE", "0")),
            timezone=os.getenv("TIMEZONE", "Asia/Hong_Kong"),
            enable_llm_summary=os.getenv("ENABLE_LLM_SUMMARY", "0") == "1",
            llm_provider=os.getenv("LLM_PROVIDER", "nim").strip(),
            llm_model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip(),
            timeframe_stack=tf_stack,  # type: ignore[arg-type]
            tf_alignment_weight=float(os.getenv("TF_ALIGN_WEIGHT", "0.4")),
            cross_engine_weight=float(os.getenv("CROSS_ENGINE_WEIGHT", "0.35")),
            data_quality_weight=float(os.getenv("DATA_QUALITY_WEIGHT", "0.25")),
            sanity_gap_pct_threshold=float(os.getenv("SANITY_GAP_PCT", "0.10")),
            sanity_min_bars_per_tf=int(os.getenv("SANITY_MIN_BARS", "14")),
        )
