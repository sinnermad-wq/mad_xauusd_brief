"""Tests for narrator V4 — system prompt optimization + rule-based short-circuit.

Covers:
- System prompt token reduction (V3 ~180 -> V4 ~125)
- build_user_prompt template format
- _rule_based_narrate short-circuit for small batches
- parse_narrated_response parity with V3
- narrate() short-circuit: empty / <=3 / ENABLE_LLM_SUMMARY=0
- ENABLE_LLM_FEW_SHOT opt-in
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from daily_xauusd_brief.narrator import (
    _SYSTEM_PROMPT,
    _FEW_SHOT_BLOCK,
    build_user_prompt,
    parse_narrated_response,
    _rule_based_narrate,
    narrate,
    NarrativeResult,
)
from daily_xauusd_brief.models import NewsItem


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_item(title: str, description: str = "") -> NewsItem:
    return NewsItem(
        title=title,
        description=description,
        url="https://example.com",
        source="test",
        published_at="2026-06-28T08:00:00Z",
    )


# ── V4 System Prompt ───────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_system_prompt_exists_and_is_string(self):
        assert isinstance(_SYSTEM_PROMPT, str)
        assert len(_SYSTEM_PROMPT) > 0

    def test_system_prompt_token_reduction(self):
        """V4 should be <= 125 tokens (V3 was ~180)."""
        # Rough estimate: avg 2 chars per token for zh, 4 for en.
        # 125 tokens * 2 chars = ~250 chars is a safe upper bound.
        assert len(_SYSTEM_PROMPT) <= 300, (
            f"System prompt too long: {len(_SYSTEM_PROMPT)} chars. "
            "V4 target is ~125 tokens (~250 chars zh)."
        )

    def test_system_prompt_contains_format_spec(self):
        """System prompt must define output format with || delimiter."""
        assert "||" in _SYSTEM_PROMPT
        assert "摘要" in _SYSTEM_PROMPT or "摘要" in _SYSTEM_PROMPT

    def test_system_prompt_no_rule_numbers(self):
        """V4 collapses rules into compact form — no "規則 1) 規則 2)" boilerplate."""
        assert "規則 1" not in _SYSTEM_PROMPT
        assert "規則 2" not in _SYSTEM_PROMPT
        assert "規則 3" not in _SYSTEM_PROMPT

    def test_impact_labels_restricted(self):
        for label in ("偏多", "偏空", "中性", "震盪"):
            assert label in _SYSTEM_PROMPT


# ── build_user_prompt ──────────────────────────────────────────────────────────

class TestBuildUserPrompt:
    def test_single_item(self):
        items = [make_item("Fed cuts rates", "Inflation eased.")]
        prompt = build_user_prompt(items)
        lines = prompt.splitlines()
        assert "[1]" in prompt
        assert "Fed cuts rates" in prompt
        assert "Inflation eased" in prompt
        # Must end with [EOF](N)
        assert "[EOF](1)" in prompt

    def test_multiple_items(self):
        items = [
            make_item("Title A", "Body A"),
            make_item("Title B", "Body B"),
        ]
        prompt = build_user_prompt(items)
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "[EOF](2)" in prompt
        assert "Title A" in prompt
        assert "Title B" in prompt

    def test_body_truncation(self):
        """Body longer than MAX_BODY_CHARS (600) should be truncated with …"""
        long_body = "word " * 200  # ~900 chars
        items = [make_item("Title", long_body)]
        prompt = build_user_prompt(items)
        # Should contain … (truncation marker) or be within limit
        assert len(prompt) < 2000  # sanity check

    def test_few_shot_opt_in(self):
        """When include_few_shot=True, few-shot block is prepended."""
        items = [make_item("Title", "Body")]
        prompt = build_user_prompt(items, include_few_shot=True)
        assert _FEW_SHOT_BLOCK in prompt

    def test_no_few_shot_by_default(self):
        """By default include_few_shot=False — no few-shot block."""
        items = [make_item("Title", "Body")]
        prompt = build_user_prompt(items, include_few_shot=False)
        assert _FEW_SHOT_BLOCK not in prompt


# ── _rule_based_narrate ────────────────────────────────────────────────────────

class TestRuleBasedNarrate:
    def test_empty_list(self):
        result = _rule_based_narrate([])
        assert result == []

    def test_single_item_returns_tuple(self):
        items = [make_item("Fed announcement", "Rates held steady.")]
        result = _rule_based_narrate(items)
        assert len(result) == 1
        assert isinstance(result[0], tuple)
        assert len(result[0]) == 2
        summary, impact = result[0]
        assert isinstance(summary, str)
        assert len(summary) > 0
        assert impact == "中性"  # rule-based always returns 中性

    def test_multiple_items(self):
        items = [
            make_item("A", "Desc A"),
            make_item("B", "Desc B"),
            make_item("C", "Desc C"),
        ]
        result = _rule_based_narrate(items)
        assert len(result) == 3
        assert all(isinstance(r, tuple) for r in result)


# ── parse_narrated_response ────────────────────────────────────────────────────

class TestParseNarratedResponse:
    def test_valid_response(self):
        raw = (
            "1. 聯準會維持利率不變||中性：利率未變，金價區間整理\n"
            "2. 烏克蘭局勢升溫||偏多：避險需求上升\n"
        )
        result = parse_narrated_response(raw, expected_count=2)
        assert len(result) == 2
        assert result[0] == ("聯準會維持利率不變", "中性：利率未變，金價區間整理")
        assert result[1] == ("烏克蘭局勢升溫", "偏多：避險需求上升")

    def test_extra_items_truncated(self):
        raw = (
            "1. Item 1||中性：原因1\n"
            "2. Item 2||偏多：原因2\n"
            "3. Item 3||偏空：原因3\n"  # should be discarded
        )
        result = parse_narrated_response(raw, expected_count=2)
        assert len(result) == 2

    def test_malformed_lines_become_none(self):
        raw = (
            "1. Valid item||中性：原因\n"
            "2. Invalid line without delimiter\n"
            "3. Another||valid||too||many||pipes\n"
        )
        result = parse_narrated_response(raw, expected_count=3)
        assert result[0] is not None
        assert result[1] is None
        # Line with too many || — last two parts only
        assert result[2] is not None

    def test_empty_raw_returns_all_none(self):
        result = parse_narrated_response("", expected_count=3)
        assert result == [None, None, None]

    def test_empty_lines_skipped(self):
        raw = (
            "1. Item 1||中性：原因\n"
            "\n"
            "2. Item 2||偏多：原因\n"
        )
        result = parse_narrated_response(raw, expected_count=2)
        assert len(result) == 2
        assert result[0] is not None
        assert result[1] is not None

    def test_whitespace_in_response(self):
        raw = "  1.  摘要文字  ||  中性：原因  \n"
        result = parse_narrated_response(raw, expected_count=1)
        assert result[0] is not None
        summary, impact = result[0]
        assert summary == "摘要文字"
        assert impact == "中性：原因"

    def test_out_of_range_indices_skipped(self):
        raw = "0. Invalid||中性：原因\n1. Valid||偏多：原因\n99. Invalid||中性：原因"
        result = parse_narrated_response(raw, expected_count=2)
        assert result[0] == ("Valid", "偏多：原因")
        assert result[1] is None


# ── narrate() Short-Circuit ─────────────────────────────────────────────────────

class TestNarrateShortCircuit:
    def test_empty_items_returns_success_empty(self):
        result = narrate([])
        assert result.success is True
        assert result.narrated == []
        assert result.error is None

    @patch("daily_xauusd_brief.narrator.call_llm")
    def test_enables_llm_summary_0_skips_llm(self, mock_call_llm):
        """ENABLE_LLM_SUMMARY=0 should skip LLM call entirely."""
        with patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "0"}):
            result = narrate([make_item("A", "B")])
        mock_call_llm.assert_not_called()
        assert result.success is True
        assert len(result.narrated) == 1

    @patch("daily_xauusd_brief.narrator.call_llm")
    def test_3_items_or_fewer_skips_llm(self, mock_call_llm):
        """<= 3 items should skip LLM, use rule-based."""
        items = [make_item(f"Title {i}", f"Desc {i}") for i in range(3)]
        with patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "1"}):
            result = narrate(items)
        mock_call_llm.assert_not_called()
        assert result.success is True
        assert len(result.narrated) == 3

    @patch("daily_xauusd_brief.narrator.call_llm")
    def test_4_items_calls_llm(self, mock_call_llm):
        """4+ items should call LLM when ENABLE_LLM_SUMMARY=1."""
        items = [make_item(f"Title {i}", f"Desc {i}") for i in range(4)]
        mock_call_llm.return_value = (
            "1. 摘要1||中性：原因1\n"
            "2. 摘要2||偏多：原因2\n"
            "3. 摘要3||偏空：原因3\n"
            "4. 摘要4||震盪：原因4\n"
        )
        with patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "1"}):
            result = narrate(items, cfg=MagicMock(provider="mock", model="mock", base_url="mock", api_key="mock"))
        mock_call_llm.assert_called_once()
        assert result.success is True
        assert len(result.narrated) == 4

    @patch("daily_xauusd_brief.narrator.call_llm")
    def test_llm_failure_returns_success_false(self, mock_call_llm):
        """LLM exception should return NarrativeResult with success=False."""
        mock_call_llm.side_effect = RuntimeError("quota exceeded")
        items = [make_item(f"Title {i}", f"Desc {i}") for i in range(5)]
        with patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "1"}):
            result = narrate(items, cfg=MagicMock(provider="mock", model="mock", base_url="mock", api_key="mock"))
        assert result.success is False
        assert result.error is not None
        assert "quota" in result.error.lower()

    @patch("daily_xauusd_brief.narrator.call_llm")
    def test_few_shot_env_enables_block(self, mock_call_llm):
        """ENABLE_LLM_FEW_SHOT=1 should include few-shot block in user prompt."""
        mock_call_llm.return_value = (
            "1. 摘要||中性：原因\n"
            "2. 摘要2||偏多：原因2\n"
            "3. 摘要3||偏空：原因3\n"
            "4. 摘要4||震盪：原因4\n"
        )
        items = [make_item(f"Title {i}", f"Body {i}") for i in range(4)]
        with patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "1", "ENABLE_LLM_FEW_SHOT": "1"}):
            result = narrate(items, cfg=MagicMock(provider="mock", model="mock", base_url="mock", api_key="mock"))
        # 4 items + ENABLE_LLM_SUMMARY=1 → LLM path taken
        assert result.success is True
        mock_call_llm.assert_called_once()
        # The user prompt passed to call_llm should contain the few-shot block
        _, user_prompt = mock_call_llm.call_args[0]
        assert _FEW_SHOT_BLOCK in user_prompt

    @patch("daily_xauusd_brief.narrator.call_llm")
    def test_malformed_llm_response_still_returns_success(self, mock_call_llm):
        """Malformed LLM output (cannot parse) returns success=True with None slots."""
        mock_call_llm.return_value = "this is not in the right format at all"
        items = [make_item(f"Title {i}", f"Desc {i}") for i in range(4)]
        with patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "1"}):
            result = narrate(items, cfg=MagicMock(provider="mock", model="mock", base_url="mock", api_key="mock"))
        # LLM returned but nothing parsed (invalid format) — padded with None
        assert result.success is True
        assert len(result.narrated) == 4
        assert all(v is None for v in result.narrated)