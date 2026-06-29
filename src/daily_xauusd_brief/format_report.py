"""報告格式化：Markdown + Telegram 繁中短版。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .compute_indicators import format_key_levels
from .models import DailyReport, NewsItem

# Tag 中英文 emoji。Tool final report 的 emoji 一致。
TAG_EMOJI = {
    "central_bank": "🏛️",
    "inflation": "📈",
    "geopolitics": "🌍",
    "usd": "💵",
    "other": "📌",
}


def _arrow(change_pct: float) -> str:
    """依漲跌回傳 emoji。"""
    if change_pct > 0:
        return "🟢"
    if change_pct < 0:
        return "🔴"
    return "⚪"


def _trend_zh(trend: str) -> str:
    return {
        "bullish": "偏多 (🟢)",
        "bearish": "偏空 (🔴)",
        "neutral": "中性 (⚪)",
    }.get(trend, "中性 (⚪)")


def _news_line(it: NewsItem, idx: int | None = None) -> str:
    """單條新聞的繁中 line。"""
    prefix = f"{idx}. " if idx is not None else "• "
    tag = TAG_EMOJI.get(it.tag, "📌")
    summary = it.summary_zh or it.title
    return f"{prefix}{tag} {summary}  _({it.source})_\n  ↳ {it.url}"


def _price_status_emoji(report: DailyReport) -> str:
    if report.price.price is None:
        return "⚠️"
    return _arrow(report.price.change_pct or 0.0)


def format_telegram(report: DailyReport, md_path: str | None = None) -> str:
    """Telegram 繁中短版。手機友善。目標 ≤ 11 行。

    規則:
    - 一 header + 三核心行：現價 / MA20 / 解讀 1 行
    - 新聞只列 3 條 (每條 1 行 短句)
    - 重點 / 風險 / 備註 各 1 行
    - 完整版 MD 路徑附加底部（點擊即開）
    - fallback 安全：價格 / 新聞失敗都唔 crash
    """
    p = report.price
    ind = report.indicators
    lines: list[str] = []

    # header
    lines.append(f"🥇 *XAUUSD 每日簡報* · {report.report_date}")
    lines.append("")

    # 價格
    emoji = _price_status_emoji(report)
    if p.price is None:
        lines.append(f"{emoji} *價格資料取得失敗*")
        if p.status.message:
            lines.append(f"    └ {p.status.message}")
    else:
        lines.append(
            f"{emoji} 現價 *${p.price:,.2f}*"
            f"（{p.change_abs:+,.2f} / {p.change_pct:+.2f}%）"
        )
    lines.append("")

    # 技術 + 解讀 (單行)
    tech_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "📊"}[ind.trend]
    tech_short = f"技術 {_trend_zh(ind.trend)}"
    if ind.ma20 is not None and p.price is not None:
        diff_pct = (p.price - ind.ma20) / ind.ma20 * 100.0
        sign = "+" if diff_pct >= 0 else ""
        tech_short += f" · MA20 ${ind.ma20:,.2f} ({sign}{diff_pct:.2f}%)"
    lines.append(f"{tech_emoji} {tech_short}")
    if report.interpretation:
        lines.append(f"↪ {report.interpretation}")
    lines.append("")

    # 新聞 (最多 3 條)
    if report.news:
        top = report.news[:3]
        lines.append(f"📰 *新聞* (Top {len(top)})")
        for i, n in enumerate(top, 1):
            short = (n.summary_zh or n.title or "").strip()
            if len(short) > 60:
                short = short[:57] + "…"
            lines.append(f"  {i}. {short}")
        lines.append("")
    else:
        if p.price is not None:
            lines.append("📰 *新聞*: 來源全數失敗，無標頭")
            lines.append("")

    # 重點 + 風險 + 備註
    if report.news_highlights:
        h = report.news_highlights.replace("\n", " ").strip()
        if len(h) > 100:
            h = h[:97] + "…"
        lines.append(f"🎯 {h}")

    # 風險提醒 (固定 heuristic: 唔夠新聞 = 潛在震盪 / 留意 Fed)
    risk = _risk_line(p, ind, report)
    if risk:
        lines.append(f"⚠️ {risk}")

    if report.notes:
        nt = report.notes.replace("\n", " ").strip()
        if len(nt) > 100:
            nt = nt[:97] + "…"
        lines.append(f"📝 {nt}")

    lines.append("")

    # footer
    lines.append(f"🕗 {report.generated_at.strftime('%H:%M')} HKT")
    if md_path:
        lines.append(f"📄 MD：`{md_path}`")
    lines.append(f"_{report.disclaimer}_")
    return "\n".join(lines).rstrip() + "\n"


def _risk_line(p: PriceSnapshot, ind: TechnicalIndicators, report: DailyReport) -> str:
    """簡單風險提醒。固定 ~1 行, 不放大噪音。"""
    parts: list[str] = []
    if ind.ma20 is not None and p.price is not None:
        diff_pct = abs((p.price - ind.ma20) / ind.ma20 * 100.0)
        if diff_pct > 3.0:
            parts.append(f"現價偏離 MA20 ({diff_pct:.1f}%),短線留意假突破")
    if report.key_levels.high_5d is not None and report.key_levels.low_5d is not None:
        rng = report.key_levels.high_5d - report.key_levels.low_5d
        if p.price is not None and rng > 0 and p.price > 0:
            rng_pct = rng / p.price * 100.0
            if rng_pct < 1.5:
                parts.append("近 5 日波幅收窄,留意方向選擇")
    if not report.news:
        parts.append("今晚關注 Fed 講話 / 通脹數據 (如有)")
    if not parts:
        return ""
    return " · ".join(parts)


def format_markdown(report: DailyReport) -> str:
    """Markdown 詳細版。支援價格資料 / 新聞資料降級。"""
    p = report.price
    ind = report.indicators

    md: list[str] = [
        f"# 🥇 XAUUSD 每日簡報 — {report.report_date}",
        "",
        f"> 報告產生於 {report.generated_at.strftime('%Y-%m-%d %H:%M')}（HKT）",
        "",
        "## 💰 價格",
        "",
    ]

    if p.price is None:
        md.append("**⚠️ 今日價格資料取得失敗**")
        md.append("")
        if p.status.message:
            md.append(f"- 原因：{p.status.message}")
        if p.status.primary_source:
            md.append(f"- 主來源：{p.status.primary_source}")
        if p.status.fallback_used:
            md.append("- 已嘗試 fallback：仍失敗")
        if p.status.primary_ok:
            md.append("- 主來源成功：但資料不完整")
        else:
            md.append("- 主來源失敗 → fallback 失敗")
        md.append("")
        md.append("技術指標與新聞仍照常輸出，請以手動查看其他平台補充。")
        md.append("")
    else:
        arrow = _arrow(p.change_pct or 0.0)
        md.append(f"- 現價：**${p.price:,.2f}** {arrow}")
        md.append(f"- 日變動：**{p.change_abs:+,.2f}**（{p.change_pct:+.2f}%）")
        md.append(f"- 資料時間：{p.as_of.strftime('%Y-%m-%d %H:%M')}")
        md.append("")

    md.append("## 📊 技術")
    md.append("")
    md.append(f"- 趨勢判讀：**{_trend_zh(ind.trend)}**")
    if ind.ma20 is not None:
        md.append(f"- 20 日均線：**${ind.ma20:,.2f}**")
    else:
        md.append("- 20 日均線：資料不足")
    md.append("")

    # 關鍵價位 + 解讀 (僅有價格時有意義)
    if p.price is not None:
        if report.interpretation or report.key_levels.prev_high is not None:
            md.append("## 🔑 今日關鍵價位")
            md.append("")
            if (
                report.key_levels.prev_high is not None
                or report.key_levels.high_20d is not None
            ):
                md.append("**價位分布**：")
                md.append("")
                md.append(format_key_levels(report.key_levels))
                md.append("")
            if report.interpretation:
                md.append("**技術解讀**：")
                md.append("")
                md.append(report.interpretation)
                md.append("")

    # 新聞區塊
    md.append("## 📰 黃金/宏觀新聞")
    md.append("")
    if report.news:
        for i, n in enumerate(report.news[:5], 1):
            md.append(f"### {i}. {n.title}")
            md.append("")
            md.append(f"- **類別**：{_tag_zh(n.tag)} {TAG_EMOJI.get(n.tag, '📌')}")
            md.append(f"- **摘要**：{n.summary_zh}")
            md.append(f"- **來源**：{n.source}（[原文]({n.url})）")
            md.append("")
    else:
        if p.price is not None:
            md.append("⚠️ 本日新聞來源全數失敗（newsapi.ai + RSS），無新聞可呈現。")
        else:
            md.append("本日未抓到黃金相關新聞。")
        md.append("")

    # 重點小結
    md.append("## 🎯 今日黃金重點")
    md.append("")
    md.append(report.news_highlights or "—")
    md.append("")

    # 備註 (使用者自填)
    md.append("## 📝 備註")
    md.append("")
    if report.notes:
        md.append(report.notes)
    else:
        md.append("_（空 — 你可以喺 `reports/notes/YYYY-MM-DD.md` 寫心得，下一次同日期 report 會自動 load。）_")
    md.append("")

    md.append("---")
    md.append("")
    md.append(f"_{report.disclaimer}_")
    return "\n".join(md)


def _tag_zh(tag: str) -> str:
    return {
        "central_bank": "央行政策",
        "inflation": "通膨數據",
        "geopolitics": "地緣政治",
        "usd": "美元 / 美債",
        "other": "其他",
    }.get(tag, "其他")


def filename_for(report_date: datetime) -> Path:
    """Markdown 報告檔名：reports/gold/YYYY-MM-DD.md。"""
    return Path("reports") / "gold" / (report_date.strftime("%Y-%m-%d") + ".md")
