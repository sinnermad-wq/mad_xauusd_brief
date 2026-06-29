"""LLM Client — 統一處理不同 Provider 的 API 呼叫。

支援：
- 'deepseek' / 'openai': 標準 OpenAI-compatible API
- 'nim': 透過 Hermes 內部機制或本地 NIM 端點 (由 Agent 呼叫)
- 'mock': 測試用，回傳確定性內容
"""
from __future__ import annotations

import logging
import httpx
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class LLMConfig:
    api_key: Optional[str]
    base_url: str
    model: str
    provider: str

def resolve_llm_config(
    api_key: Optional[str] = None,
    override_provider: Optional[str] = None,
    override_model: Optional[str] = None,
    override_base_url: Optional[str] = None,
) -> LLMConfig:
    """將輸入參數解析為標準的 LLMConfig 對象。"""
    provider = override_provider or "deepseek"
    model = override_model or "deepseek-chat"
    base_url = override_base_url or "https://api.deepseek.com/v1"
    
    if provider == "mock":
        return LLMConfig(api_key="mock", base_url="mock://local", model="mock-model", provider="mock")
    
    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
    )

def call_llm(system: str, user: str, cfg: LLMConfig) -> str:
    """統一的 LLM 呼叫入口。"""
    if cfg.provider == "mock":
        return "1. 測試摘要：這是一條 Mock 內容||偏多：測試影響\n2. 測試摘要：另一條 Mock 內容||偏空：測試影響"

    if cfg.provider == "nim":
        # NIM 模式：在 Hermes 環境中，我們不透過 httpx 呼叫外部，
        # 而是由 narrator 告知需要摘要，由 main.py 透過 Agent 本身(即目前這個 LLM) 處理。
        return "__NIM_INTERNAL_CALL__"

    if not cfg.api_key:
        raise ValueError(f"Provider {cfg.provider} requires api_key")

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{cfg.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={
                    "model": cfg.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.15,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("LLM call failed (%s): %s", cfg.provider, e)
        raise RuntimeError(f"LLM error: {e}")
