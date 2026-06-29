"""V4 Optimized Narrator — reduced token, improved cache hit.

Changes from V3:
- System prompt: ~180 → ~95 tokens (-47%)
- User prompt: fixed template, ~25 tokens saved
- Few-shot: opt-in via ENABLE_LLM_FEW_SHOT, not always sent
- Temperature lowered to 0.15 (more deterministic, fewer tokens)
- Short-circuit: ≤3 items skip LLM entirely (rule-based fallback)

Design choices (v4):
- Batch summarisation: ALL articles go into ONE prompt. Not N calls.
- Output strictly line-delimited: each line is "<idx>. <summary>||<impact>".
- Failure modes never raise to caller — NarrativeResult with success=False.
- Impact labels restricted to: 偏多 / 偏空 / 中性 / 震盪.
- Body truncated to MAX_BODY_CHARS before send.
- LLM backend via call_llm(); default DeepSeek.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from pydantic import BaseModel

from daily_xauusd_brief.llm_client import LLMConfig, call_llm, resolve_llm_config
from daily_xauusd_brief.models import NewsItem
from daily_xauusd_brief.news_summarizer import build_summary_zh, classify_tag

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 600
MAX_ARTICLES = 20
_IMPACT_LABELS = ("偏多", "偏空", "中性", "震盪")

# ── V4 Optimized System Prompt ─────────────────────────────────────────────────
# Byte-identical across runs → high prompt cache hit rate.
_SYSTEM_PROMPT = (
    "【角色】黃金/美元/利率研究員。\n"
    "【任務】新聞→繁中1-2句摘要+XAUUSD影響標籤。\n"
    "【格式】<idx>. <摘要>||<偏多|偏空|中性|震盪>：<原因≤25字，講邏輯>\n"
    "【規則】(1)繁體白話 (2)每條一行 (3)不加Markdown/表格"
)

# Few-shot examples — only loaded+sent when ENABLE_LLM_FEW_SHOT=1
# Stored as a constant string; env check at build time keeps _SYSTEM_PROMPT pure.
_FEW_SHOT_BLOCK = (
    "[1] 聯準會表示通膨仍高，暫緩降息\n"
    "  ||中性：FED論調未變，金價區間整理\n"
    "[2] 烏克蘭局勢升溫，避險資金流入黃金\n"
    "  ||偏多：地緣風險激發買盤\n"
)


class NarrativeResult(BaseModel):
    """Output of the narrator. Never raises.

    narrated[i] is the (summary, impact) pair for items[i], or None if that
    slot failed to parse. Length always equals len(items) so callers can zip.
    """

    success: bool
    narrated: list[Optional[tuple[str, str]]] = []
    error: Optional[str] = None


def build_user_prompt(items: list[NewsItem], *, include_few_shot: bool = False) -> str:
    """Build compact user prompt.

    Template (byte-identical prefix each run):
      [1] <title>
        <body>
      [2] <title>
        <body>
      ...
      [EOF](N)
    """
    parts: list[str] = []
    if include_few_shot:
        parts.append(_FEW_SHOT_BLOCK)

    for i, item in enumerate(items, 1):
        title = item.title.strip()
        body = (item.description or "").strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "…"
        parts.append(f"[{i}] {title}")
        if body:
            parts.append(f"  {body}")

    parts.append(f"[EOF]({len(items)})")
    return "\n".join(parts)


def parse_narrated_response(
    raw: str, expected_count: int
) -> list[Optional[tuple[str, str]]]:
    """Parse LLM response into list of (summary, impact) tuples.

    - extra items beyond expected_count are truncated
    - malformed lines (without '||') become None
    - labels not in _IMPACT_LABELS are kept verbatim
    """
    out: list[Optional[tuple[str, str]]] = []
    if not raw:
        return [None] * expected_count

    line_re = re.compile(r"^\s*(\d+)\.\s*(.+?)\|\|(.+?)\s*$")
    for line in raw.splitlines():
        m = line_re.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        if idx < 1 or idx > expected_count:
            continue
        while len(out) < idx - 1:
            out.append(None)
        if len(out) < idx:
            out.append(None)
        out[idx - 1] = (m.group(2).strip(), m.group(3).strip())
        if idx > expected_count:
            break

    out = out[:expected_count]
    while len(out) < expected_count:
        out.append(None)
    return out


def _rule_based_narrate(items: list[NewsItem]) -> list[Optional[tuple[str, str]]]:
    """Rule-based fallback for ≤3 items — no LLM call needed."""
    result = []
    for it in items:
        tag = classify_tag(it.title, it.description or "")
        summary = build_summary_zh(it.title, it.description or "", tag)
        result.append((summary, "中性"))
    return result


def narrate(
    items: list[NewsItem],
    *,
    api_key: str = "",
    cfg: Optional[LLMConfig] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> NarrativeResult:
    """Summarise articles into Traditional Chinese. Returns NarrativeResult.

    Short-circuit: if ENABLE_LLM_SUMMARY=0 or items ≤3, skip LLM entirely.

    Args:
        items: list of NewsItem to summarise.
        api_key: backward-compat (used if cfg is None and provider is set).
        cfg: pre-resolved LLMConfig (preferred).
        provider / base_url / model: optional overrides for resolving cfg.

    Behaviour:
    - Empty list → success=True, narrated=[]  (no LLM call)
    - ≤3 items (LLM disabled or env) → rule-based fallback, no LLM call
    - LLM raises (quota, network, auth) → success=False, narrated=[], error
    - LLM returns malformed → success=True, narrated padded with None
    """
    if not items:
        return NarrativeResult(success=True, narrated=[])

    # ── Short-circuit: rule-based for tiny batches or disabled LLM ─────────
    use_llm = os.getenv("ENABLE_LLM_SUMMARY", "1") == "1"
    if not use_llm or len(items) <= 3:
        narrated = _rule_based_narrate(items)
        logger.info("narrator: rule-based fallback for %d items", len(items))
        return NarrativeResult(success=True, narrated=narrated)

    # ── Resolve LLM config ─────────────────────────────────────────────────
    if cfg is None:
        cfg = resolve_llm_config(
            api_key=api_key or None,
            override_provider=provider,
            override_base_url=base_url,
            override_model=model,
        )

    truncated = items[:MAX_ARTICLES]
    use_few_shot = os.getenv("ENABLE_LLM_FEW_SHOT", "0") == "1"
    user_prompt = build_user_prompt(truncated, include_few_shot=use_few_shot)

    try:
        raw = call_llm(_SYSTEM_PROMPT, user_prompt, cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM call failed for narrative: %s", exc)
        return NarrativeResult(success=False, narrated=[], error=str(exc))

    parsed = parse_narrated_response(raw, expected_count=len(truncated))
    logger.info(
        "narrator: %d/%d parsed (provider=%s, model=%s, few_shot=%s)",
        sum(1 for p in parsed if p is not None),
        len(parsed),
        cfg.provider,
        cfg.model,
        use_few_shot,
    )
    return NarrativeResult(success=True, narrated=parsed, error=None)