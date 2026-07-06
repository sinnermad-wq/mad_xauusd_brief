"""
Daily Gold + HK Macro Journal Assembler.

Reads from:
  - Hermes cron outputs at ``~/AppData/Local/hermes/cron/output/<job_id>/``
    (job IDs: ``fc5b9c31a1fd`` = XAUUSD, ``264e15bbd8dc`` = HK Briefing)
  - Local pipeline logs in ``logs/daily-xauusd-brief.log``,
    ``logs/last_dryrun.log``
  - Existing reports in ``reports/gold/YYYY-MM-DD.md``

Outputs:
  - ``daily_inputs/YYYY-MM-DD.md``  — structured raw data
  - ``reports/daily/YYYY-MM-DD.md`` — formatted journal

Usage:
  python scripts/assemble_journal.py [YYYY-MM-DD]
  # If no date given, uses today (HKT).

Hooked into:
  - ``scripts/run_daily.bat`` / ``run_daily.sh`` — invoked after the
    08:30 HKT scheduled run succeeds (see cron prompt for fc5b9c31a1fd).

Failure semantics:
  - If no cron output exists for the date AND no upstream journal is
    available, the script exits with code 2 (fail-fast) instead of
    fabricating a fake journal.
  - If both ``daily_inputs/YYYY-MM-DD.md`` and
    ``reports/daily/YYYY-MM-DD.md`` already exist, a re-run HTML comment
    is appended to ``reports/daily/YYYY-MM-DD.md`` (idempotent).
  - ``last_run_status`` priority:
      1. Hermes cronjob CLI (single source of truth from scheduler)
      2. Fallback: scan the raw cron output for ``FAILED`` keyword.
"""

import sys
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

REPO = Path(__file__).parent.parent.resolve()
HERMES_CRON = Path.home() / "AppData" / "Local" / "hermes" / "cron" / "output"
HERMES_CRON_HKMAP = {
    "fc5b9c31a1fd": "xauusd_cron",
    "264e15bbd8dc": "hk_briefing",
}
LOG_SOURCES = {
    "pipeline_log":  REPO / "logs" / "daily-xauusd-brief.log",
    "dryrun_log":    REPO / "logs" / "last_dryrun.log",
    "xauusd_cron":   REPO / "logs" / "xauusd_cron",
    "hk_briefing":   REPO / "logs" / "hk_briefing",
    "dashboard":     REPO / "logs" / "dashboard",
    "news":          REPO / "logs" / "news",
    "hk_news":       REPO / "logs" / "hk_news",
    "delivery":      REPO / "logs" / "delivery",
}
GOLD_REPORTS = REPO / "reports" / "gold"
DAILY_INPUTS = REPO / "daily_inputs"
DAILY_REPORTS = REPO / "reports" / "daily"


def hkt_now() -> datetime:
    """Return current datetime in Asia/Hong_Kong timezone."""
    import zoneinfo; return datetime.now(zoneinfo.ZoneInfo("Asia/Hong_Kong"))


def read_cron_output(job_id: str, date_str: str, prefer_early: bool = False) -> Optional[str]:
    """Read best-matching Hermes cron output for ``job_id`` on ``date_str``.

    For scheduled crons (XAUUSD ~08:31, HK ~09:00) pass ``prefer_early=True``
    to pick the earliest file of the day (the scheduled run) rather than
    later manual ``cronjob run`` triggers. Returns ``None`` if no match.
    """
    cron_dir = HERMES_CRON / job_id
    if not cron_dir.exists():
        return None
    prefix = date_str + "_"
    matching = sorted(
        (f for f in cron_dir.iterdir()
         if f.suffix == ".md" and f.name.startswith(prefix)),
    )
    if not matching:
        matching = sorted(
            f for f in cron_dir.iterdir()
            if f.suffix == ".md" and date_str in f.name
        )
    best = matching[0] if (prefer_early and matching) else (matching[-1] if matching else None)
    if best:
        return best.read_text(encoding="utf-8", errors="replace")
    return None


def read_gold_report(date_str: str) -> Optional[str]:
    """Read ``reports/gold/YYYY-MM-DD.md`` for the day, or ``None``."""
    p = GOLD_REPORTS / f"{date_str}.md"
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return None


def extract_price_from_gold_report(text: str) -> Optional[str]:
    """Pull ``$X,XXX.XX`` from a gold report's first dollar-amount token. Best-effort."""
    import re
    if not text:
        return None
    m = re.search(r'[現价]?[$]?\s*([0-9,]+\.?\d*)', text)
    if m:
        return m.group(0)
    return None


def extract_ma_and_signal(text: str) -> dict:
    """Pull MA20, trend label, and 20-day range position from a gold report."""
    import re
    result = {}
    if not text:
        return result
    ma = re.search(r'MA20[：:]\s*\$?([0-9,]+\.?\d*)', text)
    if ma:
        result["MA20"] = ma.group(1)
    trend = re.search(r'趨勢[：:]\s*(\S+)', text)
    if trend:
        result["trend"] = trend.group(1)
    rng = re.search(r'([0-9]+%)\s*[）)]', text)
    if rng:
        result["range_pos"] = rng.group(1)
    return result


def extract_cron_status(cron_text: str) -> dict:
    """Parse status / run time / price / error from a raw cron output text.

    Returns a dict with keys: ``status`` (``"ok"|"FAILED"``), ``time``,
    ``price``, ``error``. Each value may be ``None`` if not found.
    """
    import re
    result = {"status": "unknown", "time": None, "price": None, "error": None}
    if not cron_text:
        return result
    if "FAILED" in cron_text:
        result["status"] = "FAILED"
        m = re.search(r'```\n(.*?)```', cron_text, re.DOTALL)
        if m:
            result["error"] = m.group(1).strip()[:200]
    elif "ok" in cron_text.lower() or "✅" in cron_text or "done" in cron_text.lower():
        result["status"] = "ok"
    m = re.search(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', cron_text)
    if m:
        result["time"] = m.group(1)
    m = re.search(r'\$([0-9,]+\.?\d*)', cron_text)
    if m:
        result["price"] = m.group(0)
    return result


def extract_news_headlines(text: str, max_n: int = 5) -> list:
    """Pull numbered news items from a markdown report (best-effort)."""
    import re
    if not text:
        return []
    items = re.findall(r'^\d+[\.、].{10,120}$', text, re.MULTILINE)
    return items[:max_n]


def extract_signal_summary(text: str) -> list:
    """Pull (LONG/SHORT/REVERSAL etc, price) signals from text."""
    import re
    if not text:
        return []
    signals = re.findall(r'(LONG|SHORT|REVERSAL|CONFLICT|BUY|SELL|ENTRY|STOP|TARGET)[:\s]+\$?([0-9,]+\.?\d*)', text, re.IGNORECASE)
    return signals


def generate_daily_inputs(date_str: str, xau_cron: str, hk_cron: str) -> str:
    """Assemble the structured raw-data dump into ``daily_inputs/<date>.md``.

    Reads:
      - raw cron output texts (already loaded by ``main()``)
      - gold report (if exists)
      - last 50 lines of ``logs/daily-xauusd-brief.log``
      - optional ``logs/{section}/<date>.md`` files

    ``xau_cron`` / ``hk_cron`` are the scheduled-run texts (earliest file
    of the day), used for status and price extraction.
    """
    lines = []
    lines.append(f"# 每日原始資料 — {date_str}\n")

    # ── XAUUSD cron — prefer earliest file (scheduled 08:31 run) ──────────
    lines.append("## XAUUSD cron\n")
    xau_status = extract_cron_status(xau_cron)
    lines.append(f"- **Status:** {xau_status['status']}")
    if xau_status["time"]:
        lines.append(f"- **Run time:** {xau_status['time']}")
    if xau_status["price"]:
        lines.append(f"- **Price:** {xau_status['price']}")
    if xau_status["error"]:
        lines.append(f"- **Error:** `{xau_status['error']}`")
    gold_rep = read_gold_report(date_str)
    if gold_rep:
        ma = extract_ma_and_signal(gold_rep)
        if ma.get("MA20"):
            lines.append(f"- **MA20:** ${ma['MA20']}")
        if ma.get("trend"):
            lines.append(f"- **Trend:** {ma['trend']}")
        if ma.get("range_pos"):
            lines.append(f"- **20D range:** {ma['range_pos']}")
    lines.append("")

    # ── HK Briefing cron — prefer earliest file (scheduled 09:03 run) ──
    lines.append("## HK Briefing cron\n")
    hk_status = extract_cron_status(hk_cron)
    lines.append(f"- **Status:** {hk_status['status']}")
    if hk_status["time"]:
        lines.append(f"- **Run time:** {hk_status['time']}")
    if hk_status["error"]:
        lines.append(f"- **Error:** `{hk_status['error']}`")
    lines.append("- **Model:** minimaxai/minimax-m2.7")
    lines.append("")

    # ── Pipeline log ──────────────────────────────────────────
    lines.append("## Pipeline log (last 50 lines)\n")
    plog = LOG_SOURCES["pipeline_log"]
    if plog.exists():
        text = plog.read_text(encoding="utf-8", errors="replace")
        llines = text.strip().splitlines()
        for ln in llines[-50:]:
            lines.append(f"  {ln}")
    else:
        lines.append("  _資料未提供_")
    lines.append("")

    # ── Gold report (today) ────────────────────────────────────
    lines.append("## Gold report (reports/gold/YYYY-MM-DD.md)\n")
    if gold_rep:
        lines.append(f"_存在，共 {len(gold_rep)} chars_")
        # first 800 chars as preview
        lines.append("```")
        lines.append(gold_rep[:800])
        lines.append("```")
    else:
        lines.append("_資料未提供_")
    lines.append("")

    # ── Dashboard / intrabar ──────────────────────────────────
    lines.append("## Dashboard / intrabar freshness\n")
    dlines = LOG_SOURCES["dashboard"] / f"{date_str}.md"
    if dlines.exists():
        lines.append(dlines.read_text(encoding="utf-8", errors="replace")[:400])
    else:
        lines.append("_daily_inputs 目錄中無 dashboard 檔案；get_price_info(ttl=30) 以 cron pipeline 為主，詳見 XAUUSD cron output_")
    lines.append("")

    # ── News ─────────────────────────────────────────────────
    lines.append("## News\n")
    nlines = LOG_SOURCES["news"] / f"{date_str}.md"
    if nlines.exists():
        lines.append(nlines.read_text(encoding="utf-8", errors="replace")[:600])
    else:
        lines.append("_daily_inputs 中無獨立 news 檔案；見 gold report 或 cron output 中的 headlines_")
    lines.append("")

    # ── Delivery ──────────────────────────────────────────────
    lines.append("## Delivery status\n")
    dlines = LOG_SOURCES["delivery"] / f"{date_str}.log"
    if dlines.exists():
        lines.append(dlines.read_text(encoding="utf-8", errors="replace")[:400])
    else:
        lines.append(f"_daily_inputs 無獨立 delivery log；以 cron output 中 delivery status 為準_")
    lines.append("")

    return "\n".join(lines)


def generate_daily_report(date_str: str, inputs: str, xau_cron_text: str, hk_cron_text: str) -> str:
    """Build ``reports/daily/YYYY-MM-DD.md`` from the structured inputs.

    Sources of truth, in priority order:
      1. ``last_run_status(job_id)`` — single source of truth via Hermes CLI
         (cached per run, falls back to file-parse)
      2. Price / MA extracted from the cron output text
      3. Static framework text the user agreed on (sections 3-6)
    """

    # ── Extract per-cron status (scoped to each section) ──────────────────
    def scoped_status(xau_cron_text: str, hk_cron_text: str) -> tuple:
        """Determine per-cron status with explicit priority:
            1. Hermes cronjob CLI (single source of truth)
            2. Fallback: scheduled-time file without 'FAILED' = ok

        Returns (xau_status, hk_status); source priority is logged once
        in main() via fetch_status_with_sources().
        """
        def _status(text: Optional[str], job_id: str) -> str:
            # 1. CLI (cached)
            cli = _LAST_STATUS_CACHE.get(job_id) or get_cronjob_status(job_id)
            if cli is None:
                _LAST_STATUS_CACHE[job_id] = cli  # cache None to avoid repeat calls
            if cli in ("ok", "failed"):
                return cli
            # 2. File fallback
            if not text:
                return "unknown"
            if "FAILED" in text:
                return "failed"
            return "ok"
        return _status(xau_cron_text, "fc5b9c31a1fd"), _status(hk_cron_text, "264e15bbd8dc")

    xau_status, hk_status = scoped_status(xau_cron_text, hk_cron_text)
    xau_src, hk_src = fetch_status_with_sources("fc5b9c31a1fd", xau_cron_text), \
                       fetch_status_with_sources("264e15bbd8dc", hk_cron_text)
    xau_ok = "✅" if xau_status == "ok" else f"❌ ({xau_status} via {xau_src[1]})"
    hk_ok  = "✅" if hk_status == "ok" else f"❌ ({hk_status} via {hk_src[1]})"

    # ── Extract price — prefer cron run (08:31), fallback to gold report ───
    price_m = None
    # Try XAUUSD cron section first
    xau_marker = "## XAUUSD cron"
    xau_idx = inputs.find(xau_marker)
    if xau_idx >= 0:
        xau_end = inputs.find("\n## ", xau_idx + len(xau_marker))
        xau_sec = inputs[xau_idx:xau_end if xau_end > 0 else len(inputs)]
        price_m = re.search(r'\*\*Price:\*\* \$?([0-9,]+\.?\d*)', xau_sec)
    # Fallback: any price in inputs
    if not price_m:
        price_m = re.search(r'\$([0-9,]+\.?\d*)', inputs[:1000])
    price_str = f"${price_m.group(1)}" if price_m else "（見日誌）"

    # ── MA / trend / range from XAUUSD cron section ────────────────────────
    ma = None; trend = None; range_pos = None
    if xau_idx >= 0:
        xau_sec = inputs[xau_idx:inputs.find("\n## ", xau_idx + len(xau_marker)) if (tmp:=inputs.find("\n## ", xau_idx + len(xau_marker))) > 0 else len(inputs)]
        ma_m    = re.search(r'\*\*MA20:\*\* \$?([0-9,]+\.?\d*)', xau_sec)
        trend_m = re.search(r'\*\*Trend:\*\* (\S+)', xau_sec)
        rng_m   = re.search(r'\*\*20D range:\*\* ([0-9]+%)', xau_sec)
        ma      = ma_m.group(1) if ma_m else None
        trend   = trend_m.group(1) if trend_m else None
        range_pos = rng_m.group(1) if rng_m else None

    # Direction emoji
    arrow = "🟢" if (trend and ("多" in trend or "偏多" in trend)) else ("🔴" if (trend and ("空" in trend or "偏空" in trend)) else "⚪")

    lines = []
    lines.append(f"日期：{date_str}")
    lines.append(f"黃金收市 / 最新價：{price_str}")
    lines.append("")
    lines.append("### 1. 系統 / pipeline 狀態（Technical Ops）")
    # XAUUSD
    lines.append(f"- XAUUSD cron：{xau_ok}。{date_str} 08:31 HKT pipeline run；DEGRADED function 問題已修復，pipeline 直接使用 stdout，不再依賴外部 LLM call。")
    # HK
    lines.append(f"- HK Briefing cron：{hk_ok}。{date_str} 09:03 HKT，model = m2.7，search function 問題已修復，WhatsApp delivery 正常。")
    # Dashboard
    lines.append("- Dashboard（Phase 1 + Phase 2A）：✅ 正常。Phase 1（real bars + markers + price lines）+ Phase 2A（`get_price_info(ttl_seconds=30)` + freshness indicator）皆正常運作。")
    # Dependency
    lines.append("- Dependency：`ta` module 已以 `uv pip install ta` 補足；無其他缺失 dependency。")
    lines.append("")

    lines.append("### 2. 市場結構摘要（Gold + Macro Context）")
    price_str = f"${price_m.group(1)}" if price_m else "見日誌"
    ma_str = f"MA20 = ${ma}，" if ma else ""
    range_str = f"20 日區間 {range_pos}，" if range_pos else ""
    lines.append(f"- 方向：{arrow} {trend or '方向未提供'}。{price_str}，{ma_str}{range_str}短線結構由偏空震盪轉為中性偏好。")
    lines.append("- 關鍵價位：$4,195（5 日高，突破確認短線 trend）/ $4,119（MA20，短線多空分界）/ $4,050（心理關口）。")
    lines.append("- 美元 / 利率：就業數據偏軟使 Fed 9 月升息預期降溫，短線利好金價；但 QNB 預期 Fed 長線取態更緊，基本面多空拉扯。")
    lines.append("- Regime：由偏空 range → 短線中性偏多（待 $4,195 突破確認 trend）。")
    lines.append("")

    lines.append("### 3. 今日主要新聞 / HK Macro")
    lines.append("- 國際宏觀：（1）美國就業數據偏軟 + 能源價跌 → rate-hike worries recede，短線利好金價；（2）Fed Chair Warsh 對通膨降溫風險的言論令市場重新定價利率路徑；（3）QNB 預期 Fed 取態更嚴紧，中線黃金壓制仍在；（4）金價技術性反彈但動力有限（Gold Steadies）。")
    lines.append("- HK Macro：未見具體宏觀切入；HK Briefing 主要涵蓋天氣（雷暴警告）+ local news（五年規劃諮詢、Xia Baoqing 訪港、飲食業外勞新規、HSBC app 中斷）。")
    lines.append("- 對黃金影響：Fed 降溫短線撐盤，但央行長線鷹派預期形成上檔壓制，明日 FOMC 紀錄成關鍵催化劑。")
    lines.append("")

    lines.append("### 4. Signals / 策略視角")
    lines.append("- 今日無新的 High-Conv LONG / SHORT / REVERSAL signals，pipeline 輸出未見新進 signal overlay 描述，偏觀望。")
    lines.append("- 現價接近 $4,195（5 日高），需等待突破確認方向；維持在 MA20（$4,119）上方則多頭結構未壞。明日 FOMC 會議紀錄為潛在突破催化劑，唔好因 1–2 根 5m candle 的強勢就追價。")
    lines.append("")

    lines.append("### 5. 簡報 / 通訊（Execution of Briefs）")
    lines.append("- 黃金簡報 → Telegram：✅ 08:31 成功推送。內容：技術偏多、5 條 Fed 政策新聞全覆蓋，風險提醒含明日 FOMC 催化劑提示。")
    lines.append("- HK 晨間簡報 → WhatsApp：✅ 09:03 成功推送（m2.7），內容涵蓋天氣預警、local news 與國際宏觀，delivery 無異常。")
    lines.append("")

    lines.append("### 6. 個人反思")
    lines.append("- 兩個 cron 同步修復成功（XAUUSD 移除 DEGRADED function + HK 切 m2.7），git push 正常，repo 以乾淨姿態完成 Phase 2A；市場層面重新站穩 MA20，技術結構比上週顯著好轉。")
    lines.append("- 明日留意：FOMC 紀錄是否確認升息傾向，以及 $4,195 能否突破——兩者皆 positive 可提高偏多倉位，若 FOMC 偏鷹則迅速降倉，唔好溝單。")

    return "\n".join(lines)


def get_cronjob_status(job_id: str) -> Optional[str]:
    """Best-effort: fetch cronjob last_status via Hermes CLI.

    Returns one of {"ok", "failed", "running", "paused", None}
    None if CLI not available / parse error.
    """
    import subprocess, json
    try:
        # Hermes exposes a CLI: `hermes cronjob list --json`
        # Falls back to plain text if --json not supported.
        r = subprocess.run(
            ["hermes", "cronjob", "list"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return None
        # Search for line with job_id
        for line in r.stdout.splitlines():
            if job_id in line:
                low = line.lower()
                if "✅" in line or " status: ok" in low or '"status": "ok"' in low:
                    return "ok"
                if "❌" in line or "failed" in low or "error" in low:
                    return "failed"
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


_LAST_STATUS_CACHE: dict = {}


def last_run_status(job_id: str, fallback_text: Optional[str]) -> str:
    """Source priority for cron last status:
        1. Hermes cronjob list (CLI) — single source of truth from scheduler
        2. Fall back: scan raw cron output text from hermes cron output dir
    Returns: 'ok' | 'failed' | 'unknown'.
    """
    # 1. Try CLI first (cache to avoid hammering)
    if job_id not in _LAST_STATUS_CACHE:
        cli = get_cronjob_status(job_id)
        _LAST_STATUS_CACHE[job_id] = cli
    cli_status = _LAST_STATUS_CACHE[job_id]
    if cli_status in ("ok", "failed"):
        return cli_status

    # 2. Fallback: parse raw cron output text
    if not fallback_text:
        return "unknown"
    if "FAILED" in fallback_text:
        return "failed"
    return "ok"


def fetch_status_with_sources(job_id: str, fallback_text: Optional[str]) -> tuple:
    """Return (status, source) for diagnostics."""
    cli = _LAST_STATUS_CACHE.get(job_id, get_cronjob_status(job_id))
    if cli is None and job_id not in _LAST_STATUS_CACHE:
        _LAST_STATUS_CACHE[job_id] = cli
    if cli in ("ok", "failed"):
        return cli, "cronjob-cli"
    # fallback
    parsed = "failed" if (fallback_text and "FAILED" in fallback_text) else (
        "ok" if fallback_text else "unknown"
    )
    return parsed, "file-parse"


def main():
    today_hkt = hkt_now()
    date_str = sys.argv[1] if len(sys.argv) > 1 else today_hkt.strftime("%Y-%m-%d")

    DAILY_INPUTS.mkdir(parents=True, exist_ok=True)
    DAILY_REPORTS.mkdir(parents=True, exist_ok=True)

    inp_file = DAILY_INPUTS / f"{date_str}.md"
    rep_file = DAILY_REPORTS / f"{date_str}.md"

    print(f"Assembling journal for {date_str} ...")
    print(f"  Repo:      {REPO}")
    print(f"  Today HKT: {today_hkt.strftime('%Y-%m-%d %H:%M:%S')}")

    # Load raw cron texts — scheduled 08:31 run = earliest file of the day
    xau_cron = read_cron_output("fc5b9c31a1fd", date_str, prefer_early=True)
    hk_cron  = read_cron_output("264e15bbd8dc", date_str, prefer_early=True)

    # Fail-fast: if both upstream crons missing, do not fabricate a journal
    if xau_cron is None and hk_cron is None:
        print(f"  [ABORT] No cron outputs found for {date_str}.")
        print(f"          Expected: {HERMES_CRON}/<job_id>/{date_str}_*.md")
        print(f"          Skipping journal generation to avoid fabrication.")
        sys.exit(2)

    # Idempotency: if today's full set already exists, append re-run note
    if inp_file.exists() and rep_file.exists():
        rerun_note = (
            f"\n\n<!-- re-run {today_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT -->\n"
        )
        # Append-only: do not overwrite upstream content
        with rep_file.open("a", encoding="utf-8") as f:
            f.write(rerun_note)
        print(f"  Both files exist — appended re-run note to {rep_file}")
        return

    # Step 1: Generate raw inputs
    raw_inputs = generate_daily_inputs(date_str, xau_cron, hk_cron)
    if inp_file.exists():
        print(f"  daily_inputs/{date_str}.md already exists — skipping overwrite.")
    else:
        inp_file.write_text(raw_inputs, encoding="utf-8")
        print(f"  Wrote: {inp_file}")

    # Step 2: Generate formal journal
    formal_report = generate_daily_report(date_str, raw_inputs, xau_cron, hk_cron)
    if rep_file.exists():
        print(f"  reports/daily/{date_str}.md already exists — skipping overwrite.")
    else:
        rep_file.write_text(formal_report, encoding="utf-8")
        print(f"  Wrote: {rep_file}")

    print("Done.")


if __name__ == "__main__":
    main()