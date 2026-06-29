"""Tests for V8.1 anomaly review MVP.

Contract:
- AnomalyDetector reads logs + history + failures.
- Returns Incident records (one per anomaly) with:
  - problem (human-readable)
  - likely_cause
  - next_step
  - severity (info | warn | critical)
  - detected_at
  - related_record_path (optional)
- Triggers covered:
  - price source failed (network exception in log)
  - news source all failed (warning in log)
  - cron repeated failure (>2 in row)
  - empty output (price & news both null but log says build succeeded)
  - delay anomaly (history delta > 4 hours from previous run)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from daily_xauusd_brief.models import (
    DailyReport,
    NewsItem,
    OhlcBar,
    PriceSnapshot,
    PriceStatus,
)
from daily_xauusd_brief.anomaly import (
    AnomalyDetector,
    Incident,
    Severity,
    detect_anomalies,
)


# --- fixtures ---------------------------------------------------------------


def _good_report(prices: list[tuple[str, float, float, str]]) -> DailyReport:
    """prices: list of (date_str, close, ma20, trend) — used for happy path."""
    from daily_xauusd_brief.models import TechnicalIndicators
    bars = [
        OhlcBar(date=datetime.fromisoformat(d).date(), open=c-5, high=c+5, low=c-8, close=c)
        for d, c, _, _ in prices
    ]
    last = bars[-1]
    prev = bars[-2]
    return DailyReport(
        symbol="XAU/USD",
        report_date=last.date,
        generated_at=datetime.now(),
        price=PriceSnapshot(
            symbol="XAU/USD",
            price=last.close,
            change_abs=round(last.close - prev.close, 2),
            change_pct=round((last.close - prev.close) / prev.close * 100, 2),
            as_of=datetime.now(),
            status=PriceStatus(primary_ok=True, message=""),
        ),
        indicators=TechnicalIndicators(ma20=prices[-1][2], trend=prices[-1][3]),
        news=[NewsItem(
            title=f"news-{i}",
            source="rss",
            url=f"https://example.com/{i}",
            description=f"desc-{i}",
        ) for i in range(3)],
    )


def _bad_report_no_price() -> DailyReport:
    from daily_xauusd_brief.models import TechnicalIndicators
    return DailyReport(
        symbol="XAU/USD",
        report_date=datetime.now().date(),
        generated_at=datetime.now(),
        price=PriceSnapshot(
            symbol="XAU/USD",
            price=None,
            change_abs=None,
            change_pct=None,
            as_of=datetime.now(),
            status=PriceStatus(primary_ok=False, message="Twelve Data API 500", fallback_used=True),
        ),
        indicators=TechnicalIndicators(ma20=4050.0, trend="neutral"),
        news=[],
    )


# --- Severity enum ----------------------------------------------------------


def test_severity_enum_has_three_levels() -> None:
    assert {s.value for s in Severity} == {"info", "warn", "critical"}


def test_incident_to_dict_roundtrip() -> None:
    inc = Incident(
        problem="價格源失敗",
        likely_cause="Twelve Data API quota exceeded",
        next_step="retry in 5min / check API key",
        severity="warn",
        detected_at=datetime(2026, 6, 26, 12, 0),
    )
    d = inc.to_dict()
    assert d["problem"] == "價格源失敗"
    assert d["severity"] == "warn"
    assert d["detected_at"] == "2026-06-26T12:00:00"


# --- AnomalyDetector --------------------------------------------------------


def test_detect_empty_when_all_good() -> None:
    """Happy path: price OK, news OK, log clean → no incidents."""
    report = _good_report([("2026-06-25", 4000, 4050, "neutral")] * 5 + [("2026-06-26", 4050, 4050, "neutral")])
    log_lines = ["INFO: 200 OK http://api.twelvedata.com/time_series"]
    detector = AnomalyDetector(
        report=report,
        log_lines=log_lines,
        recent_history=[],
    )
    incidents = detector.run_all_checks()
    assert incidents == []


def test_detects_price_source_failure() -> None:
    report = _bad_report_no_price()
    log_lines = ["ERROR: Twelve Data fetch failed: 500 Internal Server Error"]
    det = AnomalyDetector(report=report, log_lines=log_lines, recent_history=[])
    incidents = det.run_all_checks()
    problems = [i.problem for i in incidents]
    assert any("價格" in p for p in problems), f"expected price-related incident, got {incidents}"
    assert all(i.severity in ("warn", "critical") for i in incidents)


def test_detects_news_source_failure() -> None:
    report = _good_report([("2026-06-25", 4000, 4050, "neutral")] * 10)
    log_lines = [
        "WARNING: NewsAPI 抓取失敗: 500",
        "WARNING: RSS 抓取失敗: ConnectionError",
    ]
    det = AnomalyDetector(report=report, log_lines=log_lines, recent_history=[])
    incidents = det.run_all_checks()
    problems = " ".join(i.problem for i in incidents)
    assert "新聞" in problems or "news" in problems.lower()


def test_detects_repeated_cron_failure() -> None:
    """If 3+ history records in 24h failed, emit critical incident."""
    report = _good_report([("2026-06-26", 4050, 4050, "neutral")] * 5)
    now = datetime.now()
    failed_history = [
        {
            "timestamp": (now - timedelta(hours=h)).isoformat(),
            "mode": "daily",
            "xauusd_price": None,
            "trend": "neutral",
        }
        for h in [1, 6, 12]
    ]
    det = AnomalyDetector(report=report, log_lines=[], recent_history=failed_history)
    incidents = det.run_all_checks()
    assert any("cron" in i.problem.lower() or "失敗" in i.problem for i in incidents)
    assert any(i.severity == "critical" for i in incidents)


def test_detects_run_delay() -> None:
    """If no history record in last 24h, suggest cron may have stalled."""
    report = _good_report([("2026-06-26", 4050, 4050, "neutral")] * 5)
    last_ts = (datetime.now() - timedelta(hours=36)).isoformat()
    history = [{"timestamp": last_ts, "mode": "daily", "xauusd_price": 4000.0, "trend": "neutral"}]
    det = AnomalyDetector(report=report, log_lines=[], recent_history=history)
    incidents = det.run_all_checks()
    assert any("延遲" in i.problem or "stall" in i.problem.lower() or "36" in i.problem for i in incidents)


def test_detects_empty_output_anomaly() -> None:
    """If price=None AND news empty, that's a critical output anomaly."""
    report = _bad_report_no_price()  # already no_price + no_news
    det = AnomalyDetector(report=report, log_lines=["INFO: pipeline done, markdown written to /tmp/x.md"], recent_history=[])
    incidents = det.run_all_checks()
    assert any(i.severity == "critical" for i in incidents)


# --- save_incident + incidents/ folder --------------------------------------


def test_save_incident_creates_file(tmp_path: Path) -> None:
    """save_incident writes JSON into <base>/incidents/ file."""
    from daily_xauusd_brief.anomaly import save_incident
    inc = Incident(
        problem="test problem",
        likely_cause="test cause",
        next_step="test next",
        severity="warn",
        detected_at=datetime.now(),
    )
    out = save_incident(inc, base_dir=tmp_path)
    assert out.exists()
    assert out.parent == tmp_path / "incidents"
    assert out.suffix == ".json"


def test_save_incident_appends_in_run_id(tmp_path: Path) -> None:
    """Multiple incidents in same minute share run_id disambiguator."""
    from daily_xauusd_brief.anomaly import save_incident
    inc = Incident(
        problem="multi",
        likely_cause="x",
        next_step="y",
        severity="warn",
        detected_at=datetime(2026, 6, 26, 12, 30, 5),
    )
    p1 = save_incident(inc, base_dir=tmp_path)
    p2 = save_incident(inc, base_dir=tmp_path)
    assert p1 != p2
    assert (tmp_path / "incidents").glob("*.json")
    files = list((tmp_path / "incidents").glob("*.json"))
    assert len(files) == 2


# --- end-to-end: detect_anomalies from filesystem ---------------------------


def test_detect_anomalies_end_to_end(tmp_path: Path, monkeypatch):
    """Whole pipeline: read history dir + log dir, return list."""
    # Build a fake history folder with 1 record
    history_dir = tmp_path / "data" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-06-25T08-30_daily.json").write_text(
        '{"timestamp":"2026-06-25T08:30:00","mode":"daily","xauusd_price":4000.0,"trend":"neutral","top_news":[]}',
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "daily-xauusd-brief.log").write_text(
        "INFO: pipeline done, markdown written to /tmp/x.md\n"
        "WARNING: NewsAPI 抓取失敗: 500\n"
        "WARNING: RSS 抓取失敗: ConnectionError\n",
        encoding="utf-8",
    )

    # Build a report with no news
    from datetime import datetime
    from daily_xauusd_brief.models import (
        DailyReport, NewsItem, PriceSnapshot, PriceStatus, TechnicalIndicators
    )
    rep = DailyReport(
        symbol="XAU/USD",
        report_date=datetime.now().date(),
        generated_at=datetime.now(),
        price=PriceSnapshot(symbol="XAU/USD", price=4050.0, as_of=datetime.now(),
                            status=PriceStatus(primary_ok=True, message="")),
        indicators=TechnicalIndicators(ma20=4050.0, trend="neutral"),
        news=[],
    )

    with patch("daily_xauusd_brief.anomaly.Path.cwd", return_value=tmp_path):
        incidents = detect_anomalies(rep)
    assert isinstance(incidents, list)
    # Should pick up news failure from log
    assert any("新聞" in i.problem or "News" in i.problem for i in incidents)
