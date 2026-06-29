"""Backtest replay — load history, sort by timestamp, emit sorted walk-forward signals.

No future leakage guarantee:
    - Signals sorted ascending by timestamp BEFORE any lookup
    - Horizon lookup uses monotonic time index, NOT random access

Public interface:
    load_signals(spec: ReplaySpec, data_root: Path) -> list[dict]
    walk_forward(signals, price_series, horizons) -> Generator[SignalBarPair]

Both are pure functions with no side-effects; fully mock-able for tests.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Generator, Iterable, Optional

from .models import ReplaySpec, PricingSeries
from .exceptions import HistorySourceError

logger = logging.getLogger(__name__)

# ─── Supported source subdirs ─────────────────────────────────────────────
_SOURCE_SUBPATHS: dict[str, str] = {
    "fusion":      "data/history/fusion",
    "candlestick": "data/history/candlestick",
    "briefing":    "data/history",   # briefing stored in root daily files
}

# Fields extracted from each history format into a flat dict for replay
_FUSION_FIELDS = [
    "signal_id", "timestamp", "timeframe",
    "decision", "confidence", "trade_candidate",
    "consensus_label", "conflict_label", "regime_tag", "data_quality",
]
_CANDLE_FIELDS = [
    "signal_id", "timestamp", "timeframe",
    "decision", "confidence", "trade_candidate",
    "consensus_label", "conflict_label", "regime_tag", "data_quality",
]
_BRIEFING_FIELDS = [
    "signal_id", "timestamp", "timeframe",
    "decision", "confidence", "trade_candidate",
    "consensus_label", "conflict_label", "regime_tag", "data_quality",
]


# ─── Public: load signals from spec ───────────────────────────────────────
def load_signals(
    spec: ReplaySpec,
    data_root: str | Path,
) -> list[dict]:
    """Load and flatten signals from configured history sources.

    Args:
        spec: replay parameters (horizons / from_date / to_date / limit / sources)
        data_root: repo root (Path to `C:\\Users\\...\\daily-xauusd-bot`)

    Returns:
        List of flat signal dicts, sorted ascending by timestamp.
        Skips files with missing / unparseable timestamp (logged, not raised).

    Raises:
        HistorySourceError: if a named source dir doesn't exist at all.
    """
    data_root = Path(data_root)
    all_signals: list[dict] = []

    for source in spec.sources:
        sub = _SOURCE_SUBPATHS.get(source, source)
        full = data_root / sub
        if not full.exists():
            # Try root as fallback (useful for tests and ad-hoc data layouts)
            if not data_root.exists() or not any(data_root.glob("*.json")):
                logger.warning("[replay] source dir missing, skipping: %s", full)
                continue
            # Fall back: files directly in data_root
            full = data_root

        signals = _load_from_dir(source, full)
        if spec.require_trade_candidate is not None:
            signals = [
                s for s in signals
                if bool(s.get("trade_candidate")) == spec.require_trade_candidate
            ]
        all_signals.extend(signals)

    # ── sort ascending by timestamp (deterministic, no future-leak)
    all_signals.sort(key=lambda s: s.get("timestamp", ""))

    # ── date range filter
    if spec.from_date:
        all_signals = [s for s in all_signals if s.get("timestamp", "")[:10] >= spec.from_date]
    if spec.to_date:
        all_signals = [s for s in all_signals if s.get("timestamp", "")[:10] <= spec.to_date]

    # ── limit (last N signals)
    if spec.limit and spec.limit > 0:
        all_signals = all_signals[-spec.limit:]

    logger.info(
        "[replay] loaded %d signals from %s (from=%s to=%s limit=%s)",
        len(all_signals), spec.sources, spec.from_date, spec.to_date, spec.limit,
    )
    return all_signals


def _load_from_dir(source: str, full: Path) -> list[dict]:
    """Load all JSON files from `full`, apply source-specific normaliser."""
    signals: list[dict] = []
    normaliser = _NORMALISERS.get(source, _normalise_fusion)
    for f in full.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[replay] skip unparseable file %s: %s", f.name, e)
            continue
        normalised = normaliser(data)
        if normalised:
            signals.append(normalised)
    return signals


# ─── Normalisers (source → flat signal dict) ──────────────────────────────
def _normalise_fusion(data: dict) -> Optional[dict]:
    ts = data.get("timestamp") or data.get("run_timestamp") or ""
    if not ts:
        return None
    ei = data.get("execution_intent") or {}
    return {
        "signal_id":       data.get("signal_id", ""),
        "timestamp":       ts,
        "timeframe":       data.get("timeframe", "1D"),
        "decision":        ei.get("decision", "none"),
        "confidence":      data.get("fusion_confidence", data.get("confidence", 0.0)),
        "trade_candidate": data.get("trade_candidate", False),
        "consensus_label": data.get("consensus_label", ""),
        "conflict_label":  data.get("conflict_label", ""),
        "regime_tag":      data.get("regime_tag"),
        "data_quality":    data.get("data_quality_flag"),
        "_source":          "fusion",
    }


def _normalise_candlestick(data: dict) -> Optional[dict]:
    sp = data.get("source_payload") or {}
    ts = data.get("timestamp") or sp.get("timestamp") or ""
    if not ts:
        return None
    ei = data.get("execution_intent") or {}
    return {
        "signal_id":       data.get("signal_id", ""),
        "timestamp":       ts,
        "timeframe":       data.get("timeframe", "1D"),
        "decision":        ei.get("decision", "none"),
        "confidence":      data.get("confidence", 0.0),
        "trade_candidate": data.get("trade_eligible", False),
        "consensus_label": sp.get("validation", {}).get("timeframe_alignment", {}).get("label", ""),
        "conflict_label":  sp.get("validation", {}).get("cross_engine", {}).get("label", ""),
        "regime_tag":      None,
        "data_quality":    data.get("data_quality_flag"),
        "_source":         "candlestick",
    }


_NORMALISERS = {
    "fusion":      _normalise_fusion,
    "candlestick": _normalise_candlestick,
}


# ─── Public: walk-forward emit ─────────────────────────────────────────────
def walk_forward(
    signals: list[dict],
    price_series: PricingSeries,
    horizons: tuple[int, ...],
) -> Generator[tuple[dict, int, float, float, str], None, None]:
    """Yield (signal, horizon, entry_price, exit_price, outcome_reason) in time order.

    For each signal (already sorted ascending):
        entry  = price_series closest index ≤ signal timestamp
        exit   = price_series entry index + horizon_bars

    If no entry or exit → yield (signal, horizon, None, None, OUTCOME_REASON).

    Determinism: signals MUST be pre-sorted; function enforces no random access.

    Yields tuples of (signal_dict, horizon_bars, entry_price, exit_price, reason).
    """
    if not signals:
        return

    ts_to_idx = {ts: i for i, ts in enumerate(price_series.timestamps)}
    n = len(price_series)

    for signal in signals:
        sig_ts = signal.get("timestamp", "")
        entry_idx = ts_to_idx.get(sig_ts)

        if entry_idx is None:
            # signal time not in price series — skip
            for h in horizons:
                yield signal, h, None, None, "signal_ts_not_in_price_series"
            continue

        for h in horizons:
            exit_idx = entry_idx + h
            if exit_idx >= n:
                # out of window — can't compute outcome
                yield signal, h, price_series.closes[entry_idx], None, "out_of_window"
            else:
                entry_p = price_series.closes[entry_idx]
                exit_p  = price_series.closes[exit_idx]
                yield signal, h, entry_p, exit_p, "ok"