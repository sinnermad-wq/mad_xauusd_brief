"""主入口：MVP v2 + V3 M2 Candlestick Integration.

Subcommands:
    --mode briefing  生成 briefing report（預設）
    --mode candlestick 生成 candlestick analysis（V3 M2）
    --mode both      順序執行 briefing → candlestick
    --dry-run        只生成 + 預覽，不送出（任何 mode 下）
    --send-only      不重抓資料，從 cache 重發（僅 briefing）
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# ── V5 Backtest ────────────────────────────────────────────────────────────
from backtest.models import ReplaySpec, PricingSeries, BacktestRunSummary
from backtest.replay import load_signals, walk_forward
from backtest.price_source import load_pricing_series_from_candles
from backtest.evaluate import evaluate_outcomes
from backtest.calibration import compute_calibration
from backtest.breakdown import compute_breakdown
from backtest.report import write_report
from backtest.models import VERDICT_OK
_main_dir = Path(__file__).resolve().parent          # .../src/daily_xauusd_brief
_src_dir  = _main_dir.parent                         # .../src
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from .cache import load_latest, save_latest
from .compute_indicators import (
    compute_indicators,
    compute_key_levels,
    describe_price_vs_ma20,
    interpret,
)
from .config import Config
from .fetch_data import FetchError, fetch_time_series
from .fetch_news import fetch_news_main, fetch_news_legacy, fetch_rss
from .format_report import filename_for, format_markdown, format_telegram
from .history import save_history
from .models import (
    DailyReport,
    NewsItem,
    OhlcBar,
    PriceSnapshot,
    PriceStatus,
    TechnicalIndicators,
)
from .llm_client import resolve_llm_config
from .narrator import narrate
from .news_ranker import dedupe_news, rank_news
from .news_summarizer import annotate, news_highlights
from .notes import load_notes
from .telegram_sender import TelegramSendError, send_brief

logger = logging.getLogger(__name__)


# ----- Parsers -----

def _bar_from_twelve(raw: dict) -> OhlcBar | None:
    try:
        return OhlcBar(
            date=date.fromisoformat(raw["datetime"][:10]),
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _newsapi_org_to_items(articles: list[dict]) -> list[NewsItem]:
    """Legacy newsapi.org → NewsItem。"""
    items: list[NewsItem] = []
    for a in articles:
        if not a.get("title") or not a.get("url"):
            continue
        items.append(
            NewsItem(
                title=a["title"],
                source=a.get("source", "newsapi.org"),
                url=a["url"],
                published_at=None,
                description=a.get("description", ""),
            )
        )
    return items


def _newsapi_ai_to_items(articles: list[dict]) -> list[NewsItem]:
    """newsapi.ai / Event Registry normalized dict → NewsItem。"""
    items: list[NewsItem] = []
    for a in articles:
        if not a.get("title") or not a.get("url"):
            continue
        items.append(
            NewsItem(
                title=a["title"],
                source=a.get("source", "newsapi.ai"),
                url=a["url"],
                published_at=None,
                description=a.get("description", ""),
            )
        )
    return items


def _rss_to_items(entries: list[dict]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for e in entries:
        title = e.get("title") or ""
        url = e.get("url") or ""
        if not title or not url:
            continue
        items.append(
            NewsItem(
                title=title,
                source=e.get("source", "RSS"),
                url=url,
                published_at=None,
                description=e.get("description", ""),
            )
        )
    return items


def _apply_llm_summary(news_items: list[NewsItem], cfg: Config) -> None:
    """v3 option B+: 用 LLM (default DeepSeek) 做繁中新聞摘要 + 影響標籤.

    - 失敗 (quota/auth/網絡) 時: summary_zh 保留原值, 永不 raise.
    - 部分條目 parse 失敗: 對應 index 維持原值, 成功的蓋過去.
    - 預設使用 mock provider (key 唔在 .env) — narrator 走 mock 嘅 echo_response
    """
    if not news_items:
        return

    cfg_obj = resolve_llm_config(
        api_key=cfg.deepseek_api_key or None,
        override_provider=cfg.llm_provider,
        override_model=cfg.llm_model,
        override_base_url=cfg.llm_base_url,
    )

    if not cfg_obj.api_key and cfg_obj.provider != "mock":
        logger.warning(
            "LLM 設定: provider=%s 但缺 api_key, 跳過摘要 (summary_zh 保留舊值)",
            cfg_obj.provider,
        )
        return

    result = narrate(news_items, cfg=cfg_obj)
    if not result.success:
        logger.warning(
            "LLM 摘要失敗, summary_zh 保留舊值 (%s)",
            (result.error or "")[:80],
        )
        return
    applied = 0
    for item, narrated in zip(news_items, result.narrated):
        if narrated is None:
            continue
        summary, _impact = narrated
        item.summary_zh = summary
        applied += 1
    logger.info(
        "LLM applied: %d/%d (provider=%s, model=%s)",
        applied, len(news_items), cfg_obj.provider, cfg_obj.model,
    )


# ----- Build report (full pipeline) -----

async def _fetch_price_safe(cfg: Config) -> tuple[list[OhlcBar], PriceStatus]:
    """價格資料 fetch + 容錯。

    Returns: (bars, status)
    - bars: 可能是空 list
    - status: 記錄 fetch 結果 (success / partial / fail)
    """
    status = PriceStatus(primary_source="Twelve Data")
    primary_ok = False

    # ---------- primary: Twelve Data ----------
    try:
        raw = await fetch_time_series(cfg.twelve_data_api_key, symbol=cfg.symbol)
        bars = [b for b in (_bar_from_twelve(r) for r in raw) if b is not None]
        bars.sort(key=lambda b: b.date)
        if bars:
            status.message = f"主來源回 {len(bars)} 根 bars"
            status.primary_ok = True
            primary_ok = True
            return bars, status
        else:
            status.message = "主來源回傳空 bars"
    except Exception as exc:  # noqa: BLE001
        logger.error("Twelve Data fetch failed: %s", exc)
        status.message = f"主來源錯誤：{exc}"[:200]
        bars = []

    # ---------- fallback：目前就用同一 source 重試一次（模擬 fallback path） ----------
    # v3 路線：加 Alpha Vantage / yfinance etc。了大 vases lambda 這所以 包保留 function 形狀。
    try:
        raw = await fetch_time_series(cfg.twelve_data_api_key, symbol=cfg.symbol)
        bars = [b for b in (_bar_from_twelve(r) for r in raw) if b is not None]
        bars.sort(key=lambda b: b.date)
        if bars:
            status.fallback_used = True
            status.message = "通過 fallback 重試成功"
            status.primary_ok = True
            return bars, status
    except Exception as exc:  # noqa: BLE001
        logger.error("Twelve Data fallback retry failed: %s", exc)

    return [], status


async def build_report(cfg: Config, base_dir: Path) -> DailyReport:
    bars, price_status = await _fetch_price_safe(cfg)

    indicators = compute_indicators(bars)
    key_levels = compute_key_levels(bars)

    # 現價 / 日變動
    if bars and len(bars) >= 2:
        last = bars[-1]
        prev = bars[-2]
        change_abs = round(last.close - prev.close, 2)
        change_pct = round((last.close - prev.close) / prev.close * 100, 2)
        price_val: float | None = float(last.close)
        change_abs_v: float | None = change_abs
        change_pct_v: float | None = change_pct
    elif bars:
        last = bars[-1]
        price_val = float(last.close)
        change_abs_v = 0.0
        change_pct_v = 0.0
    else:
        price_val = None
        change_abs_v = None
        change_pct_v = None

    ma20_note = describe_price_vs_ma20(price_val, indicators.ma20)
    interpretation = interpret(price_val, indicators.ma20, key_levels, trend=indicators.trend)
    trend_word = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}[indicators.trend]
    if price_val is not None:
        summary = f"收盤 ${price_val:,.2f}。{ma20_note}技術面向 {trend_word}。"
    else:
        summary = f"今日價格資料取得失敗。{ma20_note}"

    # 新聞：失敗 remain 完整 catch,不會 crash
    news_items: list[NewsItem] = []
    is_uuid_key = "-" in cfg.newsapi_key
    try:
        if is_uuid_key:
            raw_main = await fetch_news_main(cfg.newsapi_key, count=15)
            news_items.extend(_newsapi_ai_to_items(raw_main))
        else:
            raw_legacy = await fetch_news_legacy(cfg.newsapi_key, count=10)
            news_items.extend(_newsapi_org_to_items(raw_legacy))
    except Exception as exc:  # noqa: BLE001
        logger.warning("NewsAPI 抓取失敗: %s", exc)
    try:
        raw_rss = await fetch_rss()
        news_items.extend(_rss_to_items(raw_rss))
    except Exception as exc:  # noqa: BLE001
        logger.warning("RSS 抓取失敗: %s", exc)

    if news_items:
        news_items = dedupe_news(news_items)
        annotate(news_items)
        news_items = rank_news(news_items, top_k=5)

    # v3: LLM 繁中摘要 (現在由 Hermes Agent NIM 驅動態處理，不再在 Python 層 call API)
    # 保持 ENABLE_LLM_SUMMARY=False 以免觸發內部 API 調用
    if cfg.enable_llm_summary:
        # 這裡保留 API 接口以備未來需要，但預設不再 call 外部 API
        pass
    else:
        logger.debug("LLM summary skipped (using Hermes Agent NIM for final delivery)")

    highlights = news_highlights(news_items)

    # 使用者自填的 notes
    report_date_str = bars[-1].date.isoformat() if bars else datetime.now().date().isoformat()
    notes_text = load_notes(base_dir, report_date_str)

    return DailyReport(
        symbol=cfg.symbol,
        report_date=bars[-1].date if bars else datetime.now().date(),
        generated_at=datetime.now(),
        price=PriceSnapshot(
            symbol=cfg.symbol,
            price=price_val,
            change_abs=change_abs_v,
            change_pct=change_pct_v,
            as_of=bars[-1].date if bars else datetime.now(),
            status=price_status,
        ),
        indicators=indicators,
        news=news_items,
        summary=summary,
        ma20_note=ma20_note,
        news_highlights=highlights,
        key_levels=key_levels,
        interpretation=interpretation,
        notes=notes_text,
    )


# ----- Markdown writer -----

def write_markdown(report: DailyReport, base_dir: Path) -> Path:
    out_path = base_dir / filename_for(report.generated_at)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(format_markdown(report), encoding="utf-8")
    return out_path


# ----- Subcommands -----

async def cmd_dry_run(cfg: Config, base_dir: Path) -> int:
    """生成 MD + cache report + print Telegram 訊息，不送出."""
    report = await build_report(cfg, base_dir)
    md_path = write_markdown(report, base_dir)
    save_latest(report, base_dir=base_dir / "data" / "cache")
    save_history(report, mode="daily", base_dir=base_dir)
    logger.info("markdown written to %s; cache saved; history updated", md_path)

    print("\n" + "=" * 60)
    print("[Telegram 訊息預覽 — 已同步至 Dashboard]")
    print("=" * 60)
    print(format_telegram(report))
    print("=" * 60)
    print(f"\n[Markdown 路徑] {md_path}")
    return 0


async def cmd_no_send(cfg: Config, base_dir: Path) -> int:
    """生成 MD + cache report，但不送出."""
    report = await build_report(cfg, base_dir)
    md_path = write_markdown(report, base_dir)
    save_latest(report, base_dir=base_dir / "data" / "cache")
    save_history(report, mode="daily", base_dir=base_dir)
    logger.info("markdown written to %s; cache saved; history updated", md_path)
    return 0


async def cmd_full(cfg: Config, base_dir: Path) -> int:
    """生成 + 寫 MD + send Telegram。"""
    report = await build_report(cfg, base_dir)
    md_path = write_markdown(report, base_dir)
    save_latest(report, base_dir=base_dir / "data" / "cache")
    save_history(report, mode="daily", base_dir=base_dir)
    logger.info("markdown written to %s; history updated", md_path)

    tg_text = format_telegram(report, md_path=str(md_path))
    try:
        result = await send_brief(
            tg_text,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
        )
        logger.info("Telegram sent via Bot API: %s", result.get("result", {}).get("message_id"))
    except TelegramSendError as exc:
        logger.error("Telegram 發送失敗: %s", exc)
        if exc.status_code:
            logger.error("HTTP status: %s, body: %s", exc.status_code, exc.body[:200])
        return 1
    return 0


async def cmd_send_only(cfg: Config, base_dir: Path) -> int:
    """從 cache 載舊 report 重發，不重抓資料。"""
    report = load_latest(base_dir=base_dir / "data" / "cache")
    if report is None:
        print("[ERROR] --send-only 但無 cache，請先跑 (--dry-run) 生成 report", file=sys.stderr)
        return 2

    md_path = base_dir / filename_for(report.generated_at)
    tg_text = format_telegram(report, md_path=str(md_path))
    print("=" * 60)
    print(f"[從 cache 重發] {md_path}, generated_at={report.generated_at}")
    print("=" * 60)
    print(tg_text)
    print("=" * 60)
    # Note: --send-only is a re-send, not a new history entry
    # (caller already has latest_report.pkl from prior --dry-run)

    try:
        result = await send_brief(
            tg_text,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
        )
        logger.info("Telegram re-sent: %s", result.get("result", {}).get("message_id"))
    except TelegramSendError as exc:
        logger.error("Telegram 發送失敗: %s", exc)
        if exc.status_code:
            logger.error("HTTP status: %s", exc.body[:200])
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="XAUUSD 每日簡報 MVP v2 + Candlestick Engine V3 M2")
    parser.add_argument("--dry-run", action="store_true", help="只生成 + 預覽，不送出")
    parser.add_argument("--no-send", action="store_true", help="只生成 + cache，不送出")
    parser.add_argument(
        "--send-only",
        action="store_true",
        help="從 cache 重發 Telegram，不重抓資料（僅 briefing）",
    )
    parser.add_argument(
        "--mode",
        choices=["briefing", "candlestick", "both", "fusion", "backtest"],
        default="briefing",
        help="briefing: 原有 briefing flow（預設）；"
             "candlestick: V3 M2 Candlestick Engine；"
             "both: 順序執行 briefing → candlestick；"
             "fusion: V4 Fusion Engine (briefing + candlestick)；"
             "backtest: V5 backtest/validation (純本地，無 API call)",
    )
    # ── Backtest mode args ──────────────────────────────────────────────────
    parser.add_argument(
        "--horizon-bars",
        default="1,3,5",
        help="Horizon bars for outcome evaluation (default: 1,3,5)",
    )
    parser.add_argument("--from-date", default=None, help="Include signals on/after YYYY-MM-DD")
    parser.add_argument("--to-date",   default=None, help="Include signals on/before YYYY-MM-DD")
    parser.add_argument("--limit",     type=int, default=None, help="Limit to last N signals")
    parser.add_argument(
        "--backtest-source",
        default="fusion",
        help="Source history to backtest: fusion (default), candlestick, or both",
    )
    parser.add_argument(
        "--no-trade-filter",
        action="store_true",
        help="Include trade_candidate=False signals (default: only True)",
    )
    parser.add_argument(
        "--only-trade-candidate",
        action="store_true",
        help="Only include trade_candidate=True signals",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for report output (default: reports/backtest/YYYY-MM-DD/)",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent.parent
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logs_dir / "daily-xauusd-brief.log", encoding="utf-8"),
        ],
    )

    try:
        cfg = Config.from_env()
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    try:
        # ── Mode routing ─────────────────────────────────────────────────────
        mode = getattr(args, "mode", "briefing")

        if mode == "candlestick":
            # V3 M2: standalone candlestick analysis
            return asyncio.run(_cmd_candlestick(cfg, base_dir, args.dry_run))

        if mode == "both":
            # Sequential: briefing → candlestick
            briefing_rc = asyncio.run(cmd_dry_run(cfg, base_dir))
            if briefing_rc != 0:
                logging.warning("[Mode:both] briefing step failed, continuing")
            candle_rc = asyncio.run(_cmd_candlestick(cfg, base_dir, args.dry_run))
            return briefing_rc or candle_rc

        if mode == "fusion":
            # V4: briefing → candlestick → fusion
            briefing_rc = asyncio.run(cmd_dry_run(cfg, base_dir))
            if briefing_rc != 0:
                logging.warning("[Mode:fusion] briefing step failed, continuing")
            candle_rc = asyncio.run(_cmd_candlestick(cfg, base_dir, args.dry_run))
            if candle_rc != 0:
                logging.warning("[Mode:fusion] candlestick step failed, continuing")
            return asyncio.run(_cmd_fusion(cfg, base_dir, args.dry_run))

        if mode == "backtest":
            # V5: backtest/validation — no LLM, no API, pure local
            _logger = logging.getLogger("backtest")
            _logger.info("[Backtest] === V5 Backtest Engine started ===")
            rc = _cmd_backtest(args, base_dir)
            _logger.info("[Backtest] done, rc=%d", rc)
            return rc

        # ── Default: briefing flow ─────────────────────────────────────────────
        if args.send_only:
            return asyncio.run(cmd_send_only(cfg, base_dir))
        if args.dry_run:
            return asyncio.run(cmd_dry_run(cfg, base_dir))
        if args.no_send:
            return asyncio.run(cmd_no_send(cfg, base_dir))
        return asyncio.run(cmd_full(cfg, base_dir))
    except FetchError as e:
        print(f"[ERROR] data fetch failed: {e}", file=sys.stderr)
        return 1


# ── V3 M2: Candlestick Engine (standalone) ──────────────────────────────────

import json as _json
import logging as _cl

from .fetch_data import fetch_time_series
from candlestick_engine import CandleEngine, EngineOutput, map_candle_to_engine_output

_candle_logger = _cl.getLogger("candlestick")


async def _cmd_candlestick(cfg, base_dir: Path, dry_run: bool = False) -> int:
    """
    V3 M2: Standalone candlestick analysis.

    Data flow:
      1. Fetch daily OHLCV (30 bars) from Twelve Data
      2. Convert list[dict] → DataFrame (oldest-first, O/H/L/C/V columns)
      3. Run CandleEngine → CandleAnalysis
      4. Map to EngineOutput (unified contract)
      5. Write to data/history/candlestick/{ts}_{run_id}_candlestick.json
      6. Print summary (dry-run) or send to Telegram
    """
    import pandas as _pd  # local import to avoid cluttering module scope

    _candle_logger.info("[Candle] === V3 M2 Candlestick Engine started ===")

    # ── 1. Fetch daily OHLCV ─────────────────────────────────────────────────
    try:
        bars = await fetch_time_series(
            api_key=cfg.twelve_data_api_key,
            symbol="XAU/USD",
            interval="1day",
            outputsize=30,
        )
    except Exception as e:
        _candle_logger.error("[Candle] fetch failed: %s", e)
        return 1

    if not bars:
        _candle_logger.error("[Candle] No bars returned from API")
        return 1

    # ── 2. Convert list[dict] → DataFrame (oldest-first, O/H/L/C/V cols) ──
    df = _pd.DataFrame(bars)
    # Twelve Data returns lowercase: datetime, open, high, low, close, volume
    df = df.rename(columns={
        "datetime": "datetime",
        "open": "O", "high": "H", "low": "L", "close": "C", "volume": "V",
    })
    for col in ("O", "H", "L", "C"):
        df[col] = _pd.to_numeric(df[col], errors="coerce")
    if "V" not in df.columns:
        df["V"] = 1.0  # Twelve Data 日線預設無 volume
    else:
        df["V"] = _pd.to_numeric(df["V"], errors="coerce").fillna(1.0)
    df = df.dropna(subset=["O", "H", "L", "C"]).reset_index(drop=True)
    # Sort oldest-first (Twelve Data returns newest-first, need to reverse)
    df = df.sort_values("datetime").reset_index(drop=True)

    _candle_logger.info(
        "[Candle] Fetched %d bars, oldest=%s newest close=%.2f",
        len(df), df["datetime"].iloc[0], float(df["C"].iloc[-1]),
    )

    # ── 2. Run CandleEngine ────────────────────────────────────────────────
    engine = CandleEngine()
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    try:
        analysis = engine.run(df, timestamp=ts)
    except Exception as e:
        _candle_logger.error("[Candle] Engine.run() failed: %s", e)
        return 1

    _candle_logger.info(
        "[Candle] Engine done | bias=%s strength=%.2f run_id=%s",
        analysis.technical_bias.value, analysis.bias_strength, analysis.run_id,
    )

    # ── 3. Map to EngineOutput ────────────────────────────────────────────
    output = map_candle_to_engine_output(analysis)
    _candle_logger.info(
        "[Candle] Mapped | bias=%s quality=%s run_id=%s",
        output.bias, output.data_quality_flag, output.run_id_short,
    )

    # ── 3b. V3 M4 Validation Layer (qualification) ────────────────────────
    try:
        from candlestick_engine.validation import validate_candlestick_output

        # Optional: fetch TF biases if other timeframes configured.
        # Phase 1: only the primary TF fed through; cross-TF added in follow-up.
        tf_biases: dict[str, str] = {cfg.timeframe_stack[0]: output.bias}

        rec = validate_candlestick_output(
            output=output,
            bars=bars,
            cfg=cfg,
            briefing_bias=None,  # wire later when Briefing payload available
            tf_biases=tf_biases,
        )

        # Attach into payload & set confidence
        output.source_payload["validation"] = rec["validation"]
        output.confidence = rec["confidence"]["final_confidence"]

        # If hard-fail, flag and downgrade data_quality_flag
        if rec["overall_status"] == "invalid":
            output.data_quality_flag = "degraded"
            _candle_logger.warning(
                "[Candle] V3 M4 hard-fail on data sanity; marked degraded"
            )

        _candle_logger.info(
            "[Candle] M4 validated status=%s confidence=%.4f",
            rec["overall_status"], output.confidence,
        )
    except Exception as e:
        _candle_logger.warning(
            "[Candle] M4 validation failed (non-fatal): %s", e
        )

    # ── 3c. V3 M5 — Populate execution-ready fields (additive, no I/O) ──
    try:
        output.populate_execution_fields()
        _candle_logger.info(
            "[Candle] M5 populated | signal_id=%s decision_ready=%s "
            "trade_eligible=%s intent_decision=%s",
            output.signal_id, output.decision_ready, output.trade_eligible,
            output.execution_intent.get("decision", "none"),
        )
    except Exception as e:
        _candle_logger.warning(
            "[Candle] M5 populate failed (non-fatal): %s", e
        )

    # ── 4. Write history ──────────────────────────────────────────────────
    history_dir = base_dir / "data" / "history" / "candlestick"
    history_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{ts}_{output.run_id}_candlestick.json"
    filepath = history_dir / filename

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            _json.dump(output.to_dict(), f, ensure_ascii=False, indent=2)
        _candle_logger.info("[Candle] History saved: %s", filename)
    except Exception as e:
        _candle_logger.error("[Candle] History write failed: %s", e)
        return 1

    # ── 4b. Write HTML report (V3 M3) ───────────────────────────────────────
    try:
        from candlestick_engine.report import write_candlestick_report

        report_dir = base_dir / "reports" / "candlestick"
        report_date = ts[:10] if ts else None
        html_path = write_candlestick_report(output, bars, report_dir, report_date)
        _candle_logger.info("[Candle] HTML report saved: %s", html_path.name)
    except Exception as e:
        _candle_logger.warning("[Candle] HTML report write failed (non-fatal): %s", e)

    # ── 5. Output ────────────────────────────────────────────────────────
    if dry_run:
        summary = (
            f"[DRY-RUN] Candlestick Analysis\n"
            f"  bias      : {output.bias}\n"
            f"  strength  : {output.bias_strength:.4f}\n"
            f"  structure : {analysis.structure_state.value}\n"
            f"  run_id    : {output.run_id}\n"
            f"  history   : {filename}\n"
            f"  patterns  : {len(analysis.detected_patterns)}\n"
            f"  summary   : {output.explanation_zh}\n"
            f"\n--- Telegram preview ---\n"
            f"{output.to_telegram_zh()}"
        )
        print(summary)
        _candle_logger.info("[Candle] Dry-run complete")
        return 0

    # Send to Telegram if token available
    if cfg.telegram_token and cfg.telegram_chat_id:
        try:
            from . import send_telegram
            msg = output.to_telegram_zh()  # V3 M3: use contract formatter
            send_telegram(cfg.telegram_token, cfg.telegram_chat_id, msg)
            _candle_logger.info("[Candle] Telegram sent")
        except Exception as e:
            _candle_logger.warning("[Candle] Telegram failed (non-fatal): %s", e)
    else:
        # Always log the Telegram-formatted message (preview) even in dry-run
        _candle_logger.info(
            "[Candle] Telegram preview (no token):\n%s",
            output.to_telegram_zh(),
        )

    _candle_logger.info("[Candle] === Candlestick Engine completed ===")
    return 0


# ── V4 Fusion Command ─────────────────────────────────────────────────────


_fusion_logger = logging.getLogger("fusion")


async def _cmd_fusion(cfg, base_dir: Path, dry_run: bool = False) -> int:
    """V4 Fusion Engine command.

    Loads latest candlestick + optional briefing payloads from history,
    builds FusionInput, runs FusionEngine, writes FusionOutput JSON to
    data/history/fusion/. Never touches a broker.

    Returns 0 on success, 1 on hard error, 2 on graceful skip.
    """
    _fusion_logger.info("[Fusion] === V4 Fusion Engine started ===")

    cand_dir = base_dir / "data" / "history" / "candlestick"
    brief_dir = base_dir / "data" / "history" / "briefing"

    # 1. Load latest candle
    candle_files = (
        sorted(cand_dir.glob("*_candlestick.json"), reverse=True)
        if cand_dir.exists() else []
    )
    if not candle_files:
        _fusion_logger.warning(
            "[Fusion] no candlestick history — run --mode candlestick first"
        )
        return 2
    try:
        candle_payload = _json.loads(candle_files[0].read_text(encoding="utf-8"))
        candle_output = EngineOutput.from_dict(candle_payload)
    except Exception as e:
        _fusion_logger.error("[Fusion] failed to load candlestick: %s", e)
        return 1

    # 2. Load latest briefing (optional)
    briefing_payload = None
    brief_files = (
        sorted(brief_dir.glob("*_daily.json"), reverse=True)
        if brief_dir.exists() else []
    )
    if brief_files:
        try:
            briefing_payload = _json.loads(
                brief_files[0].read_text(encoding="utf-8")
            )
        except Exception as e:
            _fusion_logger.warning(
                "[Fusion] briefing read failed (will degrade): %s", e
            )

    # 3. Build fusion input (handle missing briefing gracefully)
    from fusion_engine import FusionEngine, build_fusion_input

    fi = build_fusion_input(
        candle_output=candle_output,
        briefing_payload=briefing_payload,
        cfg=cfg,
        run_id=candle_output.run_id,
    )

    # 4. Run fusion
    try:
        # V4: cfg is daily_xauusd_brief.Config; FusionEngine wants its own
        # FusionConfig. Use defaults for V4 (V5+ will bridge weights).
        fusion_out = FusionEngine(cfg=None).fuse(fi)
    except Exception as e:
        _fusion_logger.error("[Fusion] Engine.fuse failed: %s", e)
        return 1

    # 5. Write history JSON
    from fusion_engine.io import write_fusion_record

    fusion_dir = base_dir / "data" / "history" / "fusion"
    try:
        out_path = write_fusion_record(
            fusion_out, fusion_dir, ts=fusion_out.timestamp
        )
        _fusion_logger.info(
            "[Fusion] history saved: %s | bias=%s conf=%.4f trade=%s",
            out_path.name, fusion_out.fusion_bias,
            fusion_out.fusion_confidence, fusion_out.trade_candidate,
        )
    except Exception as e:
        _fusion_logger.error("[Fusion] history write failed: %s", e)
        return 1

    # 6. Dry-run preview
    if dry_run:
        from fusion_engine.formatter import format_fusion_telegram_zh
        print("--- Fusion preview ---")
        print(format_fusion_telegram_zh(fusion_out))
        _fusion_logger.info("[Fusion] dry-run complete")
        return 0

    # 7. Telegram (optional, additive)
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        try:
            from . import send_telegram
            from fusion_engine.formatter import format_fusion_telegram_zh
            msg = format_fusion_telegram_zh(fusion_out)
            send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
            _fusion_logger.info("[Fusion] Telegram sent")
        except Exception as e:
            _fusion_logger.warning(
                "[Fusion] Telegram failed (non-fatal): %s", e
            )

    _fusion_logger.info("[Fusion] === Fusion Engine completed ===")
    return 0


# ─── Backtest entry (V5) ────────────────────────────────────────────────────
def _cmd_backtest(args, base_dir: Path) -> int:
    """Run V5 backtest/validation — pure local, no LLM call."""
    import logging
    import traceback
    from datetime import datetime, timezone

    _log = logging.getLogger("backtest")

    # ── parse horizons ──────────────────────────────────────────────────────
    try:
        horizons = tuple(int(h.strip()) for h in args.horizon_bars.split(","))
    except Exception:
        _log.error("[Backtest] --horizon-bars must be comma-separated ints, e.g. 1,3,5")
        return 1

    # ── ReplaySpec ───────────────────────────────────────────────────────────
    sources = tuple(s.strip() for s in args.backtest_source.split(","))

    require_tc: bool | None = None
    if args.only_trade_candidate:
        require_tc = True
    elif args.no_trade_filter:
        require_tc = None   # include both
    else:
        require_tc = True   # default: only trade_candidate=True

    spec = ReplaySpec(
        horizons=horizons,
        from_date=args.from_date,
        to_date=args.to_date,
        limit=args.limit,
        sources=sources,
        require_trade_candidate=require_tc,
        include_none_in_trades=False,
    )

    _log.info(
        "[Backtest] spec: horizons=%s sources=%s from=%s to=%s limit=%s",
        horizons, sources, spec.from_date, spec.to_date, spec.limit,
    )

    # ── load signals ──────────────────────────────────────────────────────────
    try:
        signals = load_signals(spec, base_dir)
    except Exception as e:
        _log.error("[Backtest] load_signals failed: %s", e)
        traceback.print_exc()
        return 1

    if not signals:
        _log.warning("[Backtest] no signals loaded for spec=%s", spec)
        # Still produce INSUFFICIENT_DATA report
        _print_insufficient_summary(spec, 0, {})
        return 0

    _log.info("[Backtest] loaded %d signals", len(signals))

    # ── load price series ─────────────────────────────────────────────────────
    candle_dir = base_dir / "data" / "history" / "candlestick"
    try:
        price_series = load_pricing_series_from_candles(candle_dir)
    except Exception as e:
        _log.error("[Backtest] price source failed: %s", e)
        traceback.print_exc()
        return 1

    if len(price_series) < 2:
        _log.error("[Backtest] price_series too short (%d bars)", len(price_series))
        return 1

    _log.info("[Backtest] price_series: %d bars", len(price_series))

    # ── walk forward + evaluate ────────────────────────────────────────────────
    wf_tuples = walk_forward(signals, price_series, horizons)
    outcomes_list = list(evaluate_outcomes(wf_tuples, include_none_decision=False))

    if not outcomes_list:
        _log.warning("[Backtest] no outcomes generated (check horizon within price window)")
        _print_insufficient_summary(spec, len(signals), {})
        return 0

    outcome_dicts = [o.to_dict() for o in outcomes_list]

    # ── horizon stats ─────────────────────────────────────────────────────────
    horizon_stats: dict[int, dict] = {}
    for h in horizons:
        h_rows = [d for d in outcome_dicts if d["horizon_bars"] == h and d["outcome_reason"] == "ok"]
        if h_rows:
            n = len(h_rows)
            hits = sum(1 for r in h_rows if r["direction_correct"])
            sr = [r["signed_return"] for r in h_rows if r["signed_return"] is not None]
            horizon_stats[h] = {
                "n": n,
                "hit_rate": round(hits / n, 4),
                "avg_signed_return": round(sum(sr) / len(sr), 6) if sr else 0.0,
            }

    # ── skipped summary ──────────────────────────────────────────────────────
    from collections import Counter
    reason_counts = Counter(d["outcome_reason"] for d in outcome_dicts)
    skipped = tuple(reason_counts.items())

    # ── calibration ──────────────────────────────────────────────────────────
    from backtest.models import BacktestRunSummary as BRS
    cal_report = compute_calibration(outcomes_list)

    # ── verdict ───────────────────────────────────────────────────────────────
    verdict = VERDICT_OK if cal_report.n_total >= 10 else "INSUFFICIENT_DATA"

    # ── BacktestRunSummary ────────────────────────────────────────────────────
    summary = BRS(
        spec=spec,
        n_signals_loaded=len(signals),
        n_outcomes=len(outcomes_list),
        skipped=skipped,
        horizon_stats=horizon_stats,
        calibration=cal_report,
        verdict=verdict,
    )

    # ── output dir ────────────────────────────────────────────────────────────
    output_dir: Path | None
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = base_dir / "reports" / "backtest" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── write reports ─────────────────────────────────────────────────────────
    paths = write_report(summary, outcome_dicts, output_dir, dry_run=args.dry_run)

    if args.dry_run:
        summary_md = _build_summary_text(summary, cal_report, outcome_dicts)
        print("\n" + summary_md)
    else:
        _log.info("[Backtest] outputs: outcomes=%s calibration=%s summary=%s",
                  paths["outcomes"], paths["calibration"], paths["summary"])

    # ── summary print ─────────────────────────────────────────────────────────
    _print_horizon_table(horizon_stats)
    print(f"\nVerdict: {verdict}  |  Signals: {len(signals)}  |  Outcomes: {len(outcomes_list)}")
    return 0


def _print_insufficient_summary(spec, n_signals, horizon_stats):
    cal_data = {
        "ece": 0.0, "brier": 0.0,
        "n_total": 0, "n_long": 0, "n_short": 0, "n_none": 0,
        "by_trade_candidate_hit_rate": {},
        "by_consensus_hit_rate": {},
        "by_conflict_hit_rate": {},
    }
    print(f"\n=== Backtest INSUFFICIENT_DATA ===")
    print(f"spec: horizons={spec.horizons} sources={spec.sources}")
    print(f"signals loaded: {n_signals}")
    print(f"outcomes: 0")
    print("reason: no signals or horizon exceeds price history window")


def _build_summary_text(summary, cal_report, outcomes_data) -> str:
    """Build summary text for --dry-run (stdout)."""
    from backtest.report import _render_summary_md
    return _render_summary_md(summary, cal_report.to_dict(), outcomes_data)


def _print_horizon_table(horizon_stats):
    print(f"\n{'Horizon':<10} {'n':>6} {'Hit Rate':>10} {'Avg Signed Ret':>16}")
    print("-" * 46)
    for h in sorted(horizon_stats):
        s = horizon_stats[h]
        print(f"{h}-bar     {s['n']:>6} {s['hit_rate']:>10.1%} {s['avg_signed_return']:>+16.6f}")


def _fmt_candle_telegram(output, analysis) -> str:
    """Format EngineOutput as Telegram message."""
    emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(output.bias, "⚪")
    pattern_lines = ""
    if analysis.detected_patterns:
        pstr = " ".join(f"• {p.description_zh}" for p in analysis.detected_patterns[:3])
        pattern_lines = f"\n📊 型態：{pstr}"
    breakout_line = ""
    bs = analysis.breakout_state
    if bs.breakout_confirmed:
        breakout_line = (
            f"\n🚀 突破：已確認 {'向上' if bs.breakout_type.value == 'break_up' else '向下'}"
        )
    elif bs.breakout_watch:
        breakout_line = f"\n👁️ 觀察：突破警戒中 (@ {bs.breakout_watch_level:.1f})"

    return (
        f"{emoji} *XAUUSD Candlestick*\n"
        f"bias: *{output.bias.upper()}* ({output.bias_strength:.0%})\n"
        f"structure: `{analysis.structure_state.value}`\n"
        f"RSI(14): `{analysis.rsi_14:.1f}` ATR: `{analysis.atr_14:.1f}`\n"
        f"{pattern_lines}"
        f"{breakout_line}\n"
        f"---\n"
        f"{output.explanation_zh}\n"
        f"[run_id:{output.run_id_short}]"
    )


if __name__ == "__main__":
    sys.exit(main())
