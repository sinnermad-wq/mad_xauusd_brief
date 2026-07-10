"""Snapshot loader + convenience runner.

Auto-discovers data paths under ``data/`` and produces a snapshot
with minimal caller configuration.

Example::

    from strategy_health import load_latest_snapshot
    snap = load_latest_snapshot()
    print(snap.health_status, snap.suggestions)
"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .approval import APPROVAL_FILE
from .models import StrategyHealthConfig
from .snapshot_builder import build_health_snapshot


DATA_DIR = "data"
FUSION_HISTORY_DIR = "data/fusion_history"
BACKTEST_DIR = "data/backtest"


def _latest_json(directory: str) -> str | None:
    if not os.path.isdir(directory):
        return None
    files = glob.glob(os.path.join(directory, "*.json"))
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def _load_json(path: str | None) -> Mapping[str, Any] | None:
    if not path or not os.path.exists(path):
        return None
    try:
        return json.loads(open(path).read())
    except Exception:
        return None


def _load_fusion_signals(limit: int = 100) -> List[Mapping[str, Any]]:
    if not os.path.isdir(FUSION_HISTORY_DIR):
        return []
    files = glob.glob(os.path.join(FUSION_HISTORY_DIR, "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    signals: List[Mapping[str, Any]] = []
    for fp in files[:limit]:
        try:
            data = json.loads(open(fp).read())
            if isinstance(data, list):
                signals.extend(data)
            elif isinstance(data, Mapping):
                for k in ("signals", "history", "records", "entries"):
                    if isinstance(data.get(k), list):
                        signals.extend(data[k])
                        break
                else:
                    signals.append(data)
        except Exception:
            pass
    return signals[-limit:]


def _load_backtest_report() -> Mapping[str, Any] | None:
    path = _latest_json(BACKTEST_DIR)
    return _load_json(path)


def load_latest_snapshot(
    cfg: StrategyHealthConfig | None = None,
    approval_file_path: str = APPROVAL_FILE,
) -> "StrategyHealthSnapshot":
    """Discover and load the latest available data, then compute a snapshot.

    This is the primary entry point for one-off manual runs::

        snap = load_latest_snapshot()
        print(snap.health_status)
    """
    fusion_path = _latest_json(FUSION_HISTORY_DIR)
    backtest_path = _latest_json(BACKTEST_DIR)

    fusion_signals = _load_fusion_signals()
    backtest_report = _load_backtest_report()

    return build_health_snapshot(
        backtest_report=backtest_report,
        fusion_signals=fusion_signals if fusion_signals else None,
        fusion_history_path=fusion_path,
        backtest_report_path=backtest_path,
        candlestick_snapshot_path=None,
        cfg=cfg,
        approval_file_path=approval_file_path,
    )


def load_snapshot_from_paths(
    *,
    fusion_history_path: str | None = None,
    backtest_report_path: str | None = None,
    cfg: StrategyHealthConfig | None = None,
    approval_file_path: str = APPROVAL_FILE,
) -> "StrategyHealthSnapshot":
    """Load from explicit paths (useful for cron / CLI use)."""
    fusion_signals: List[Mapping[str, Any]] = []
    if fusion_history_path and os.path.exists(fusion_history_path):
        data = _load_json(fusion_history_path)
        if isinstance(data, list):
            fusion_signals = data
        elif isinstance(data, Mapping):
            for k in ("signals", "history", "records", "entries"):
                if isinstance(data.get(k), list):
                    fusion_signals = data[k]
                    break

    backtest_report = _load_json(backtest_report_path)

    return build_health_snapshot(
        backtest_report=backtest_report,
        fusion_signals=fusion_signals if fusion_signals else None,
        fusion_history_path=fusion_history_path,
        backtest_report_path=backtest_report_path,
        candlestick_snapshot_path=None,
        cfg=cfg,
        approval_file_path=approval_file_path,
    )