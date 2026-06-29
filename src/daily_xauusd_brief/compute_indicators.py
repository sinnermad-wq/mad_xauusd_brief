"""技術指標計算：MA20 + 趨勢 + 關鍵價位 + 解讀。"""

from __future__ import annotations

import pandas as pd

from .models import KeyLevels, OhlcBar, TechnicalIndicators


def bars_to_dataframe(bars: list[OhlcBar]) -> pd.DataFrame:
    """OhlcBar list ‑ pandas DataFrame（按日期排序）。"""
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame([b.model_dump() for b in bars])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df


def compute_indicators(bars: list[OhlcBar]) -> TechnicalIndicators:
    """計算 MA20。資料不足時，回返 None。"""
    df = bars_to_dataframe(bars)
    if df.empty or len(df) < 20:
        return TechnicalIndicators()

    close = df["close"]
    ma20 = close.rolling(20).mean().iloc[-1]
    last_close = float(close.iloc[-1])

    # 趨勢判讀 MVP version：簡潔判斷、現價在 MA20 上 = bullish，下 = bearish。
    if pd.notna(ma20):
        trend = "bullish" if last_close > ma20 else "bearish"
        ma20_val: float | None = float(ma20)
    else:
        trend = "neutral"
        ma20_val = None

    return TechnicalIndicators(
        ma20=ma20_val,
        trend=trend,
    )


def describe_price_vs_ma20(price: float | None, ma20: float | None) -> str:
    """一句話描述現價同 MA20 嘅關係。

    繁體中文，適合 Telegram / Markdown 嵌入。
    例：「現價 $2,350.42 高於 20 日均線 $2,340.00，差距 +$10.42 (+0.45%)。」
    """
    if price is None:
        return "現價資料未能取得。"
    if ma20 is None:
        return "資料不足，無法計算 20 日均線。"

    diff = price - ma20
    pct = (diff / ma20 * 100) if ma20 else 0.0
    direction = "高於" if diff > 0 else ("低於" if diff < 0 else "等於")

    return (
        f"現價 ${price:,.2f} {direction} 20 日均線 ${ma20:,.2f}"
        f"，差距 {diff:+,.2f} ({pct:+.2f}%)。"
    )


# ----- 關鍵價位 -----

def compute_key_levels(bars: list[OhlcBar]) -> KeyLevels:
    """從最近 N 根日線 bars 計算關鍵價位。

    - prev_high / prev_low：前一日（最後一根就踢的前者）高低
    - 5 日 / 20 日高低：最近 N 根 window

    資料不足時，對應字段 = None。
    """
    kl = KeyLevels()
    if len(bars) < 1:
        return kl

    if len(bars) >= 2:
        prev = bars[-2]
        kl.prev_high = prev.high
        kl.prev_low = prev.low

    if len(bars) >= 5:
        last5 = bars[-5:]
        kl.high_5d = max(b.high for b in last5)
        kl.low_5d = min(b.low for b in last5)

    if len(bars) >= 20:
        last20 = bars[-20:]
        kl.high_20d = max(b.high for b in last20)
        kl.low_20d = min(b.low for b in last20)

    return kl


def _fmt_pct(p: float | None) -> str:
    if p is None:
        return "—"
    return f"{p:+.2f}%"


def interpret(
    price: float | None,
    ma20: float | None,
    levels: KeyLevels,
    trend: str = "neutral",
) -> str:
    """一句 human-friendly 的技術解讀 (heuristic)。

    Inputs 可空。會輸出一段繁中 plan-language。
    """
    if price is None:
        return "今日價格資料取得失敗，無法產生技術解讀。"

    parts: list[str] = []
    # MA20 summary
    if ma20 is not None:
        diff = price - ma20
        if abs(diff) / ma20 < 0.005:
            parts.append("現價與 20 日均線接近，處於均線震盪區間")
        elif diff > 0:
            parts.append(f"現價站於 20 日均線之上 ({_fmt_pct((diff / ma20) * 100)})，短線結構偏多")
        else:
            parts.append(f"現價跌於 20 日均線之下 ({_fmt_pct((diff / ma20) * 100)})，短線結構偏空")
    else:
        parts.append("20 日均線資料不足，僅依關鍵價位解讀")

    # Closeness to high/low: 在區間高位 / 中段 / 低位
    if levels.high_20d is not None and levels.low_20d is not None:
        rng = levels.high_20d - levels.low_20d
        if rng > 0:
            pos = (price - levels.low_20d) / rng
            if pos >= 0.8:
                parts.append(f"現價位於 20 日區間上沿 ({(pos * 100):.0f}%)，注意假突破風險")
            elif pos >= 0.5:
                parts.append(f"現價位於 20 日區間中上段 ({(pos * 100):.0f}%)")
            elif pos >= 0.2:
                parts.append(f"現價位於 20 日區間中下段 ({(pos * 100):.0f}%)")
            else:
                parts.append(f"現價位於 20 日區間下沿 ({(pos * 100):.0f}%)，留意下方承接")

    # Volatility hint: 5 日 high-low vs 20 日 high-low
    if (
        levels.high_5d is not None and levels.low_5d is not None
        and levels.high_20d is not None and levels.low_20d is not None
    ):
        rng5 = levels.high_5d - levels.low_5d
        rng20 = levels.high_20d - levels.low_20d
        if rng20 > 0 and rng5 / rng20 >= 0.7:
            parts.append("近期波動擴大，留意區間突破")

    return "。".join(parts) + "。"


def format_key_levels(levels: KeyLevels) -> str:
    """繁中一行行格式 key levels。"""
    rows: list[tuple[str, float | None]] = [
        ("前日高", levels.prev_high),
        ("前日低", levels.prev_low),
        ("5 日高", levels.high_5d),
        ("5 日低", levels.low_5d),
        ("20 日高", levels.high_20d),
        ("20 日低", levels.low_20d),
    ]
    return "\n".join(f"- {name}：**${v:,.2f}**" if v is not None else f"- {name}：—"
                     for name, v in rows)
