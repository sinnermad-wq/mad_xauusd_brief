"""新聞 heuristic 摘要（繁中 1–2 句）。

不依赖 LLM。代則是產生「重點 → 對金價的可能影響」的繁中口訁語，並
加上 tag + relevance_score，供 ranker 使用。

v3 可換以 Hermes LLM 調用。
"""

from __future__ import annotations

from .models import NewsItem
from .news_ranker import classify_tag, relevance_score

TAG_ZH = {
    "central_bank": "央行政策",
    "inflation": "通膨",
    "geopolitics": "地緣政治",
    "usd": "美元/美債",
    "other": "其他",
}

# tag 對金價的「預設影響」繁中 context
TAG_GOLD_IMPACT = {
    "central_bank": "聯準會/主要央行取同往是金價最重要的動能；若偏向鴿派（降息、可能轉鴿）有助金價，反之鴿派有助。",
    "inflation": "通膨升溫一般推高金價，降溫令金價頽壓。須留意 CB 對此的言論。",
    "geopolitics": "風險偏好走避，黃金同樣是避險領頭；緊張局勢上升一般推升金價。",
    "usd": "美元同美債殖利率同金價逆向：美元強/殖利率升 ⇒ 金價受壓，反之金價受惠。",
    "other": "其他事項對金價影響有限，但作為背景參考仍有用。",
}


def build_summary_zh(title: str, description: str, tag: str) -> str:
    """組合繁中 1– 2 句摘要。

    第一句：該消息類型的背景 / 事實。
    第二句：對金價可能方向的判讀。
    """
    # 去 description 內拿首個有意義的句子
    clean = " ".join((description or "").split())
    snippet = clean[:80] + ("…" if len(clean) > 80 else "") if clean else ""
    impact = TAG_GOLD_IMPACT.get(tag, TAG_GOLD_IMPACT["other"])

    if snippet:
        return f"事件：{snippet} 影響機制：{impact}"
    # 拼拼一句以「title」充為事件
    return f"標題重點：{title}。{impact}"


def annotate(items: list[NewsItem]) -> list[NewsItem]:
    """填上 tag/summary_zh/relevance_score，in-place + return。"""
    for it in items:
        tag = classify_tag(it.title, it.description or "")
        it.tag = tag  # type: ignore[assignment]
        it.relevance_score = relevance_score(it.title, it.description or "")
        it.summary_zh = build_summary_zh(it.title, it.description or "", tag)
    return items


def news_highlights(items: list[NewsItem]) -> str:
    """產「今日黃金重點」小結（2–3 句繁中）。"""
    if not items:
        return "本日未抓取到與黃金 / 央行 / 通膨 / 美元 相關的重大新聞。"

    # 按 tag 統計，前幾面個主要 tag 是信息點。
    counts: dict[str, int] = {}
    for it in items:
        counts[it.tag] = counts.get(it.tag, 0) + 1

    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:3]
    tag_fragments = [f"{TAG_ZH.get(t, t)} {n}條" for t, n in top]
    main = "、".join(tag_fragments) if tag_fragments else "新聞面資訊有限"

    # 取得 top 1 標題作為「主故事」
    headline = items[0].title if items else ""
    return (
        f"本日黃金/宏觀訊息以 {main} 佔主。\n"
        f"重點事件：{headline}\n"
        f"買賣面提醒：須隔並關注以上事件在金價上的傳導路径，避給單一消息過度解讀。"
    )
