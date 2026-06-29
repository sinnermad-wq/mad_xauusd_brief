"""Tiny deterministic mock LLM for tests.

Used when an OpenAI-compatible backend isn't available (test environment, CI).
Echoes a numbered list per the same contract narrator.parse_narrated_response
expects. Pure stdlib — no network.
"""
from __future__ import annotations

import re


def echo_response(system_prompt: str, user_prompt: str) -> str:
    """Synthesise a valid narrated response based on titles in the user prompt.

    For each "[N] 標題：<title>" line, emit:
       N. 測試摘要：<title>||偏多：測試影響 N
    """
    pattern = re.compile(r"\[(\d+)\]\s*標題：(.+)")
    titles: list[tuple[int, str]] = []
    for line in user_prompt.splitlines():
        m = pattern.match(line)
        if m:
            titles.append((int(m.group(1)), m.group(2).strip()))
    if not titles:
        return ""
    out = []
    for idx, title in titles:
        short = (title[:40] + "…") if len(title) > 40 else title
        out.append(f"{idx}. 測試摘要：{short}||偏多：測試影響 {idx}")
    return "\n".join(out)
