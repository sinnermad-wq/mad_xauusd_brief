"""Tests for news narrator (LLM-based Traditional Chinese summarization).

Contract:
- summarizer takes a list of articles (≤ 20) and returns list of NewsNarrated
- output is Traditional Chinese (繁體中文)
- summary has 1–2 sentences
- impact line has phrasing that maps cleanly: 偏多/偏空/中性/震盪
- on quota / network / parse failure, returns None sentinel (caller falls back)
- batch summarisation is called with all articles in ONE prompt (not N calls)
- response is parsed strictly: each item must contain '||' delimiter between summary and impact
"""
from __future__ import annotations

from unittest.mock import patch

from daily_xauusd_brief.llm_client import LLMConfig
from daily_xauusd_brief.models import NewsItem
from daily_xauusd_brief.narrator import (
    NarrativeResult,
    narrate,
    parse_narrated_response,
)

# --- fixtures ---------------------------------------------------------------


def _mk_article(title: str, body: str = "default body", source: str = "Reuters") -> NewsItem:
    return NewsItem(
        title=title,
        url=f"https://example.com/{hash(title) & 0xffffffff}",
        source=source,
        description=body,
    )


# --- shared mock LLM config --------------------------------------------------

_MOCK_CFG = LLMConfig(
    api_key="mock-key",
    base_url="mock://local",
    model="mock-model",
    provider="mock",
)


# --- parse_narrated_response (pure, easy to test first) -----------------------


def test_parse_response_valid_well_formed() -> None:
    """Given a well-formed LLM response, parse to list of (summary, impact)."""
    raw = (
        "1. 美國通脹數據高於預期，市場對聯準會降息預期降溫。||偏空：通脹黏著使實質利率走高，金價承壓。\n"
        "2. 中東局勢升溫，避險情緒上升。||偏多：地緣風險推升避險需求。\n"
        "3. 美元指數回落至關鍵支撐位。||偏多：美元走弱有利金價表現。\n"
    )
    out = parse_narrated_response(raw, expected_count=3)
    assert len(out) == 3, f"expected 3 entries, got {len(out)}"
    summaries, impacts = zip(*out)
    # summaries & impacts all non-empty
    assert all(s.strip() for s in summaries)
    assert all(i.strip() for i in impacts)
    # impact taxonomy respected
    for imp in impacts:
        assert any(label in imp for label in ("偏多", "偏空", "中性", "震盪")), (
            f"impact label missing: {imp}"
        )
    assert "偏空" in impacts[0]
    assert "偏多" in impacts[1]


def test_parse_response_truncates_extra_items() -> None:
    """If LLM returns too many items, truncate to expected_count."""
    raw = "\n".join(f"{i}. 摘要{i}||偏多：影響{i}" for i in range(1, 6))
    out = parse_narrated_response(raw, expected_count=3)
    assert len(out) == 3


def test_parse_response_drops_malformed_lines() -> None:
    """Lines without '||' delimiter are silently dropped (return as None)."""
    raw = (
        "1. 第一條摘要||偏多：影響一\n"
        "這是胡言亂語沒有分隔符\n"  # malformed
        "3. 第三條摘要||偏空：影響三\n"
    )
    out = parse_narrated_response(raw, expected_count=3)
    # Expect: line1 ok, line2 dropped, line3 ok → 2 valid + 1 None
    assert out[0] is not None
    assert out[1] is None
    assert out[2] is not None
    assert len(out) == 3


def test_parse_response_empty_string_returns_all_none() -> None:
    out = parse_narrated_response("", expected_count=2)
    assert out == [None, None]


def test_parse_response_recognizes_neutral_label() -> None:
    raw = "1. 歐洲央行按兵不動，符合預期。||中性：本次會議未改變貨幣政策路徑。"
    out = parse_narrated_response(raw, expected_count=1)
    assert out[0] is not None
    assert "中性" in out[0][1]


def test_parse_response_recognizes_range_label() -> None:
    raw = "1. 美元與美債同步走升，金價夾在區間內整理。||震盪：多空拉扯，方向待確認。"
    out = parse_narrated_response(raw, expected_count=1)
    assert "震盪" in out[0][1]


# --- narrate() (LLM-calling, mocked) -----------------------------------------


def _make_items(n: int) -> list[NewsItem]:
    return [
        _mk_article(
            title=f"Article {i}",
            body=f"Body of article {i}. Talks about Fed policy and gold prices.",
        )
        for i in range(n)
    ]


def test_narrate_calls_llm_once_for_batch() -> None:
    """5 articles → 1 LLM call (not 5). Verify by counting call count."""
    items = _make_items(5)
    with patch("daily_xauusd_brief.narrator.call_llm") as mock:
        mock.return_value = "\n".join(
            f"{i}. 摘要{i}||偏多：影響{i}" for i in range(1, 6)
        )
        result = narrate(items, cfg=_MOCK_CFG)
    assert mock.call_count == 1
    assert isinstance(result, NarrativeResult)
    assert result.success is True
    assert len(result.narrated) == 5


def test_narrate_returns_zero_count_on_quota_error() -> None:
    """If LLM raises a quota/network exception, return NarrativeResult with success=False."""
    items = _make_items(3)
    with patch(
        "daily_xauusd_brief.narrator.call_llm",
        side_effect=RuntimeError("429 quota exceeded"),
    ):
        result = narrate(items, cfg=_MOCK_CFG)
    assert result.success is False
    assert result.narrated == []
    assert "429" in (result.error or "")


def test_narrate_returns_zero_count_on_empty_input() -> None:
    """Empty input → empty output, no LLM call."""
    result = narrate([], cfg=_MOCK_CFG)
    assert result.success is True
    assert result.narrated == []


def test_narrate_falls_back_when_some_items_unparseable() -> None:
    """If 2 out of 3 parse OK, output 3 slots with None for the bad one."""
    items = _make_items(3)
    raw = "1. 摘要一||偏多：影響一\n胡言亂語\n3. 摘要三||偏空：影響三\n"
    with patch("daily_xauusd_brief.narrator.call_llm", return_value=raw):
        result = narrate(items, cfg=_MOCK_CFG)
    assert result.success is True
    assert len(result.narrated) == 3
    assert result.narrated[0] is not None
    assert result.narrated[1] is None
    assert result.narrated[2] is not None


def test_narrate_prompt_includes_all_titles() -> None:
    """Prompt must contain every article title so model knows what to summarise."""
    items = [
        _mk_article("TITLE_A about Fed"),
        _mk_article("TITLE_B about DXY"),
        _mk_article("TITLE_C about inflation"),
    ]
    captured: dict = {}
    def fake_call(system: str, user: str, **kwargs) -> str:
        captured["system"] = system
        captured["user"] = user
        return "1. 摘要||偏多：影響\n2. 摘要||偏多：影響\n3. 摘要||偏多：影響\n"

    with patch(
        "daily_xauusd_brief.narrator.call_llm",
        side_effect=fake_call,
    ):
        narrate(items, cfg=_MOCK_CFG)
    user = captured["user"]
    for item in items:
        assert item.title in user, f"title missing from user_prompt: {item.title}"
    # system prompt should be Traditional Chinese instructions
    sys = captured["system"]
    assert "繁體中文" in sys
    assert "XAUUSD" in sys or "金價" in sys


def test_narrate_truncates_long_body() -> None:
    """If article body exceeds MAX_BODY_CHARS, truncate before sending to LLM."""
    long_body = "x" * 5000
    items = [_mk_article("Long article", body=long_body)]
    captured: dict = {}
    def fake_call(system: str, user: str, **kwargs) -> str:
        captured["user"] = user
        return "1. 摘要||偏多：影響\n"

    with patch(
        "daily_xauusd_brief.narrator.call_llm",
        side_effect=fake_call,
    ):
        narrate(items, cfg=_MOCK_CFG)
    # Prompt should contain a truncated version, not the full 5000 chars
    assert "x" * 5000 not in captured["user"]
    # Should still contain the truncated body
    assert "x" * 200 in captured["user"]


def test_narrate_uses_mock_provider_when_cfg_is_mock() -> None:
    """When cfg.provider == 'mock', narrator calls call_llm without raising.

    Verifies the provider abstraction: same call_llm interface works for any
    provider (deepseek, mock, future ones)."""
    items = _make_items(2)
    with patch("daily_xauusd_brief.narrator.call_llm") as mock:
        mock.return_value = (
            "1. 摘要一||偏多：影響一\n"
            "2. 摘要二||偏空：影響二\n"
        )
        result = narrate(items, cfg=_MOCK_CFG)
    assert result.success is True
    assert mock.call_count == 1
    # Verify cfg was passed through, not reconstructed
    call_kwargs = mock.call_args.kwargs
    assert call_kwargs["cfg"].provider == "mock"
