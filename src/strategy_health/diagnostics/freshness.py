"""Data freshness / integrity diagnostic.

Inspects file mtimes + per-source presence + structural integrity of
all required inputs.  Returns one ``StrategyDiagnostic`` whose
``metrics`` lists source-by-source status:

    {
      "fusion_history": {"present": True,  "age_minutes": 12, "state": "ok"},
      "backtest":       {"present": True,  "age_minutes": 60, "state": "warn"},
      "candlestick":    {"present": False, "age_minutes": None, "state": "unknown"},
      "merged_signal_records": {"present": True, "count": 47}
    }
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..models import (
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_UNKNOWN,
    SEVERITY_WARN,
    SOURCE_BACKTEST,
    SOURCE_CANDLESTICK,
    SOURCE_FILE_MTIME,
    SOURCE_FUSION_HISTORY,
    StrategyDiagnostic,
)


def _age_minutes(path: str, now_utc: datetime) -> Optional[float]:
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return None
    return round((now_utc - mtime).total_seconds() / 60.0, 2)


def _state_for_age(age_min: Optional[float], ttl_min: int) -> str:
    if age_min is None:
        return "unknown"
    if age_min <= ttl_min:
        return "ok"
    if age_min <= ttl_min * 2:
        return "warn"
    return "critical"


def _coerce_records(rs: Any) -> List[Mapping[str, Any]]:
    if rs is None:
        return []
    if isinstance(rs, list):
        return [r for r in rs if isinstance(r, Mapping)]
    if isinstance(rs, Mapping):
        # accept {"records": [...]}, {"signals": [...]}, {"history": [...]}
        for k in ("records", "signals", "history", "entries"):
            if isinstance(rs.get(k), list):
                return [r for r in rs[k] if isinstance(r, Mapping)]
    return []


def compute_freshness_diagnostic(
    *,
    fusion_history_path: str | None,
    backtest_report_path: str | None,
    candlestick_snapshot_path: str | None,
    merged_signals: Any = None,
    cfg=None,
    now_utc: datetime | None = None,
) -> StrategyDiagnostic:
    """Run freshness / integrity checks on the supporting files.

    Returns a single diagnostic with per-source ``state``.
    """
    fusion_ttl = int(getattr(cfg, "fusion_history_ttl_minutes", 90)) if cfg else 90
    back_ttl = int(getattr(cfg, "backtest_report_ttl_minutes", 240)) if cfg else 240
    candle_ttl = int(getattr(cfg, "candlestick_snapshot_ttl_minutes", 90)) if cfg else 90

    now = now_utc or datetime.now(tz=timezone.utc)

    rows: Dict[str, Dict[str, Any]] = {}

    f_age = _age_minutes(fusion_history_path or "", now)
    rows[SOURCE_FUSION_HISTORY] = {
        "path": fusion_history_path or "",
        "age_minutes": f_age,
        "state": "critical" if not fusion_history_path else _state_for_age(f_age, fusion_ttl),
        "ttl_minutes": fusion_ttl,
    }

    b_age = _age_minutes(backtest_report_path or "", now)
    rows[SOURCE_BACKTEST] = {
        "path": backtest_report_path or "",
        "age_minutes": b_age,
        "state": "critical" if not backtest_report_path else _state_for_age(b_age, back_ttl),
        "ttl_minutes": back_ttl,
    }

    c_age = _age_minutes(candlestick_snapshot_path or "", now)
    rows[SOURCE_CANDLESTICK] = {
        "path": candlestick_snapshot_path or "",
        "age_minutes": c_age,
        "state": "critical" if not candlestick_snapshot_path else _state_for_age(c_age, candle_ttl),
        "ttl_minutes": candle_ttl,
    }

    records = _coerce_records(merged_signals)
    rows["merged_signal_records"] = {
        "present": bool(merged_signals is not None),
        "count": len(records),
        "state": "critical" if (merged_signals is None or not records) else "ok",
    }

    states = [v["state"] for v in rows.values()]
    if "critical" in states:
        severity = SEVERITY_CRITICAL
        reasons = ("stale_or_missing_inputs",)
    elif "warn" in states:
        severity = SEVERITY_WARN
        reasons = ("input_ages_above_threshold",)
    elif "unknown" in states:
        severity = SEVERITY_UNKNOWN
        reasons = ("unknown_source_state",)
    else:
        severity = SEVERITY_OK
        reasons = ("freshness_within_threshold",)

    metrics: Dict[str, Any] = {"sources": rows, "now_utc": now.isoformat()}

    summary_text = (
        f"freshness: fusion_history={rows[SOURCE_FUSION_HISTORY]['state']} "
        f"({rows[SOURCE_FUSION_HISTORY]['age_minutes']}m), "
        f"backtest={rows[SOURCE_BACKTEST]['state']} "
        f"({rows[SOURCE_BACKTEST]['age_minutes']}m), "
        f"candlestick={rows[SOURCE_CANDLESTICK]['state']} "
        f"({rows[SOURCE_CANDLESTICK]['age_minutes']}m), "
        f"signals={rows['merged_signal_records']['count']}; verdict={severity}"
    )

    return StrategyDiagnostic(
        name="freshness",
        severity=severity,
        summary=summary_text,
        metrics=metrics,
        reasons=tuple(reasons),
        source=SOURCE_FILE_MTIME,
    )
