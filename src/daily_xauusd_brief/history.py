"""歷史記錄 — V6 結構化 JSON 落地 + review 查詢.

每當 daily / intraday / event briefing 跑完, 寫
``data/history/{timestamp}_{mode}.json``. Dashboard 讀呢個資料夾顯示總覽。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from .models import DailyReport

logger = logging.getLogger(__name__)

Mode = Literal["daily", "intraday", "event", "unknown"]


def _to_history_record(report: DailyReport, mode: str) -> dict:
    """把 DailyReport 序列化做 V6 JSON 結構."""
    p = report.price
    return {
        "timestamp": report.generated_at.isoformat(),
        "mode": mode,
        "xauusd_price": p.price,
        "daily_change_abs": p.change_abs,
        "daily_change_pct": p.change_pct,
        "technical_summary": report.ma20_note or report.interpretation,
        "trend": report.indicators.trend,
        "ma20": report.indicators.ma20,
        "top_news": [
            {
                "title": n.title,
                "summary_zh": n.summary_zh,
                "source": n.source,
                "url": n.url,
                "tag": n.tag,
            }
            for n in report.news[:5]
        ],
        "risk_notes": report.interpretation,
        "final_summary": report.news_highlights or report.summary,
        "notes": report.notes,
    }


def save_history(
    report: DailyReport, mode: str, base_dir: Path | None = None
) -> Path:
    """Persist structured JSON record.

    File name: ``data/history/{YYYY-MM-DDTHH-MM-SS}_{mode}.json``.
    Returns the path written (for logging).
    """
    base = Path(base_dir) if base_dir else Path.cwd()
    history_dir = base / "data" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    ts = report.generated_at
    fname = f"{ts.strftime('%Y-%m-%dT%H-%M-%S')}_{mode}.json"
    path = history_dir / fname

    record = _to_history_record(report, mode)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("history saved: %s (%d bytes)", path, path.stat().st_size)
    return path


def list_history(
    base_dir: Path | None = None,
    since_days: int | None = None,
    mode: str | None = None,
) -> list[dict]:
    """Load & filter JSON records (most-recent first)."""
    base = Path(base_dir) if base_dir else Path.cwd()
    history_dir = base / "data" / "history"
    if not history_dir.exists():
        return []

    cutoff: datetime | None = None
    if since_days is not None:
        cutoff = datetime.now() - timedelta(days=since_days)

    out: list[dict] = []
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("history: bad JSON at %s: %s", path, exc)
            continue
        if mode and data.get("mode") != mode:
            continue
        if cutoff:
            try:
                ts = datetime.fromisoformat(data["timestamp"])
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
        out.append(data)
    return out


def review_summary(
    base_dir: Path | None = None, since_days: int = 7
) -> dict:
    """Generate review summary across ``since_days`` window.

    Output shape:
        {
          'window_days': int,
          'record_count': int,
          'modes': {'daily': N, 'intraday': M, ...},
          'recurring_themes': [...],   # tag counts top-N
          'risk_pattern': set of recurring strings,
        }
    """
    records = list_history(base_dir=base_dir, since_days=since_days)
    payload = {
        "window_days": since_days,
        "record_count": len(records),
        "modes": _modes_count(records),
        "recurring_themes": _theme_counts(records, top=5),
        "risk_pattern": _recurring_risks(records),
    }
    return payload


# ----- helpers --------------------------------------------------------------


def _modes_count(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        m = r.get("mode", "unknown")
        out[m] = out.get(m, 0) + 1
    return out


def _theme_counts(records: list[dict], top: int) -> list[dict]:
    counts: dict[str, int] = {}
    for r in records:
        for n in r.get("top_news", []):
            tag = n.get("tag") or "other"
            counts[tag] = counts.get(tag, 0) + 1
    return [
        {"tag": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda x: -x[1])[:top]
    ]


def _recurring_risks(records: list[dict]) -> list[str]:
    """Return risk_notes patterns that appear in >= 2 records."""
    counts: dict[str, int] = {}
    for r in records:
        risk = (r.get("risk_notes") or "").strip()
        if len(risk) > 100 or not risk:
            continue
        counts[risk] = counts.get(risk, 0) + 1
    out = sorted(counts.items(), key=lambda x: -x[1])
    return [k for k, v in out if v >= 2][:5]
