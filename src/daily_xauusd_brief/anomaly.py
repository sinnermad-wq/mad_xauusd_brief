"""V8.1 Anomaly / Incident Review MVP.

Reads:
- the current report (state)
- recent history records (data/history/*.json)
- recent log lines (logs/daily-xauusd-brief.log)

Returns a list of Incidents describing detected anomalies, each in
Traditional Chinese with a structured:

- problem: human-readable
- likely_cause: 1-line guess
- next_step: concrete remediation
- severity: info | warn | critical
- detected_at: ISO timestamp
- related_record_path: optional path into history/

Saved into ``<base>/incidents/{timestamp}_{slug}.json`` so the dashboard
(v7) and future alert skill can surface them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

Severity = Literal["info", "warn", "critical"]


class Incident(dict):
    """Lightweight dict-subclass; the to_dict hop is built-in.

    Kept minimal: the dashboard / alert consumer just reads .problem / .severity.
    """

    def __init__(
        self,
        problem: str,
        likely_cause: str,
        next_step: str,
        severity: Severity,
        detected_at: datetime,
        related_record_path: Optional[str] = None,
    ) -> None:
        super().__init__(
            problem=problem,
            likely_cause=likely_cause,
            next_step=next_step,
            severity=severity,
            detected_at=detected_at.isoformat(),
            related_record_path=related_record_path,
        )

    # Convenience properties so callers can stay declarative
    @property
    def problem(self) -> str:
        return self["problem"]

    @property
    def severity(self) -> str:
        return self["severity"]


@dataclass
class AnomalyDetector:
    """Run anomaly checks against a current report.

    - report: today's DailyReport
    - log_lines: tail of log file (most recent first or raw; we scan all)
    - recent_history: list of history dicts (chronological or reverse OK)
    """

    report: object
    log_lines: list[str] = field(default_factory=list)
    recent_history: list[dict] = field(default_factory=list)
    now: datetime = field(default_factory=datetime.now)

    # --- public entry point -------------------------------------------------

    def run_all_checks(self) -> list[Incident]:
        incidents: list[Incident] = []
        incidents.extend(self._check_price_source())
        incidents.extend(self._check_news_source())
        incidents.extend(self._check_repeated_cron_failure())
        incidents.extend(self._check_run_delay())
        incidents.extend(self._check_empty_output())
        return incidents

    # --- individual checks -------------------------------------------------

    def _check_price_source(self) -> list[Incident]:
        price = getattr(self.report, "price", None)
        if price is None:
            return []
        status = getattr(price, "status", None)
        if status is not None and getattr(status, "primary_ok", True):
            return []
        message = getattr(status, "message", "") or "unknown"
        severity: Severity = "critical" if price.price is None else "warn"
        return [
            Incident(
                problem=f"價格資料源發生問題：{message[:60]}",
                likely_cause="外部行情 API (Twelve Data / Alpha Vantage / yfinance) 連線逾時 / quota / 5xx。",
                next_step="檢查 logs 中 Twelve Data 連線紀錄；必要時加 fallback source 或重試。",
                severity=severity,
                detected_at=self.now,
            )
        ]

    def _check_news_source(self) -> list[Incident]:
        joined = "\n".join(self.log_lines)
        newsapi_fail = "NewsAPI" in joined and "抓取失敗" in joined
        rss_fail = "RSS" in joined and "抓取失敗" in joined
        if not (newsapi_fail and rss_fail):
            return []
        return [
            Incident(
                problem="新聞來源全數失敗，無新聞可呈現。",
                likely_cause="NewsAPI.ai 配額用盡 / RSS feed 連線失敗 / 兩者同時掛掉。",
                next_step="驗證 NEWSAPI_KEY 配額、切換備用 RSS 或手動輸入今日焦點。",
                severity="warn",
                detected_at=self.now,
            )
        ]

    def _check_repeated_cron_failure(self) -> list[Incident]:
        recent_24h = []
        cutoff = self.now - timedelta(hours=24)
        for rec in self.recent_history:
            ts_str = rec.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue
            if ts >= cutoff:
                recent_24h.append(rec)

        failed_24h = [r for r in recent_24h if r.get("xauusd_price") is None]
        if len(failed_24h) < 3:
            return []
        return [
            Incident(
                problem=f"過去 24 小時內有 {len(failed_24h)} 次 build 失敗，cron 可能已停擺。",
                likely_cause="持續 API quota 耗盡或上游服務中斷未恢復。",
                next_step="手動跑一次 --dry-run 驗證；若仍失敗則調整時段或評估 quota 方案。",
                severity="critical",
                detected_at=self.now,
            )
        ]

    def _check_run_delay(self) -> list[Incident]:
        if not self.recent_history:
            return []
        try:
            last = max(
                (datetime.fromisoformat(r["timestamp"]) for r in self.recent_history
                 if r.get("timestamp")),
                key=lambda d: d,
            )
        except ValueError:
            return []
        gap = self.now - last
        if gap < timedelta(hours=24):
            return []
        hours = int(gap.total_seconds() // 3600)
        return [
            Incident(
                problem=f"上一次 briefing 已過 {hours} 小時，cron 可能未依排程運作。",
                likely_cause="Hermes cron job 暫停 / 系統休眠 / pipeline 無人觸發。",
                next_step="用 hermes cronjob list 檢查並 resume；或手動跑 --dry-run 重建資料。",
                severity="warn",
                detected_at=self.now,
            )
        ]

    def _check_empty_output(self) -> list[Incident]:
        """Critical if price AND news are both empty but report claims success."""
        price = getattr(self.report, "price", None)
        no_price = price is None or price.price is None
        no_news = not getattr(self.report, "news", [])
        # Only trigger if log says the pipeline "succeeded" (no error markers)
        joined = "\n".join(self.log_lines)
        pipeline_seems_ok = (
            "markdown written" in joined or "report 生成完成" in joined
        )
        if no_price and no_news and pipeline_seems_ok:
            return [
                Incident(
                    problem="Pipeline 完成但無價格、無新聞，輸出為空。",
                    likely_cause="fallback 路徑全部失效，但 log 未 raise 導致 silent pass。",
                    next_step="加強 mock / fallback coverage；對 empty output 加 assert 強制 fail。",
                    severity="critical",
                    detected_at=self.now,
                )
            ]
        return []


# --- save incident ----------------------------------------------------------


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", "-", text.strip().lower())
    return s[:50] or "incident"


def save_incident(incident: Incident, base_dir: Path | None = None) -> Path:
    """Write one incident JSON to ``<base>/incidents/{ts}_{slug}.json``.

    If multiple incidents share the same minute, a counter suffix is appended
    to keep filenames unique.
    """
    base = Path(base_dir) if base_dir else Path.cwd()
    out_dir = base / "incidents"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.fromisoformat(incident["detected_at"])
    base_name = f"{ts.strftime('%Y-%m-%dT%H-%M-%S')}_{_slug(incident['problem'])}"
    candidate = out_dir / f"{base_name}.json"
    n = 1
    while candidate.exists():
        candidate = out_dir / f"{base_name}_{n}.json"
        n += 1
    candidate.write_text(
        json.dumps(incident, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("incident saved: %s", candidate)
    return candidate


def save_incidents(incidents: list[Incident], base_dir: Path | None = None) -> list[Path]:
    return [save_incident(i, base_dir=base_dir) for i in incidents]


# --- end-to-end helper ------------------------------------------------------


def _read_log_tail(log_path: Path, max_lines: int = 200) -> list[str]:
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return text.splitlines()[-max_lines:]


def detect_anomalies(
    report: object,
    base_dir: Path | None = None,
    max_log_lines: int = 200,
) -> list[Incident]:
    """Detect anomalies for the given report, reading files from disk.

    Returns a list of Incidents — caller decides whether to persist them.
    """
    from .history import list_history
    base = Path(base_dir) if base_dir else Path.cwd()
    history = list_history(base_dir=base, since_days=7)
    log_path = base / "logs" / "daily-xauusd-brief.log"
    log_lines = _read_log_tail(log_path, max_lines=max_log_lines)
    det = AnomalyDetector(
        report=report,
        log_lines=log_lines,
        recent_history=history,
    )
    return det.run_all_checks()
